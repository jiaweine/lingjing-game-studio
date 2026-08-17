from __future__ import annotations

import asyncio
import copy
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Awaitable, Callable

from worldforge.envs import BalanceLabEnv, get_scenario
from worldforge.models import ActionKind, DecisionFrame, GameAction, RunConfig, RunSummary, RuntimeEvent, Skill

from .counterfactual import CounterfactualBrancher
from .event_store import EventStore
from .evolver import FailureDrivenEvolver
from .memory import EpisodicMemory, OutcomeRecord
from .planner import AdaptivePlanner
from .plugin import PluginDescriptor, PluginRegistry
from .policy import GroupRelativePolicyOptimizer, PolicyGroup, WorldForgePolicy
from .recursive import RecursiveAgentScheduler
from .sandbox import ActionSandbox
from .selfplay import PopulationSelfPlay
from .skill_bank import SkillBank
from .verifier import StateVerifier

EventSink = Callable[[RuntimeEvent], Awaitable[None]]


class WorldForgeEngine:
    """World-state-native autonomous game runtime.

    The canonical world is never mutated by speculative branches. Decisions combine the local
    policy, state-conditioned specialists, skill priors and memory, then pass through
    counterfactual evaluation, sandbox checks and verification before being committed.
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        skill_bank: SkillBank | None = None,
        memory: EpisodicMemory | None = None,
    ) -> None:
        db_path = Path(db_path)
        self.events = EventStore(db_path)
        self.skills = skill_bank or SkillBank()
        self.memory = memory or EpisodicMemory()
        self.verifier = StateVerifier()
        self.policy_path = db_path.with_name("worldforge_policy.json")
        self.policy_model = WorldForgePolicy.load_or_bootstrap(self.policy_path)
        self.policy_optimizer = GroupRelativePolicyOptimizer()
        self.planner = AdaptivePlanner(self.skills, self.memory, self.policy_model)
        self.brancher = CounterfactualBrancher(self.planner, self.verifier)
        self.evolver = FailureDrivenEvolver(self.skills)
        self.sandbox = ActionSandbox()
        self.selfplay = PopulationSelfPlay(self.skills)
        self.recursive = RecursiveAgentScheduler()
        self.plugins = PluginRegistry()
        self._evolution_lock = asyncio.Lock()
        self._mount_defaults()

    def _mount_defaults(self) -> None:
        self.plugins.mount(
            PluginDescriptor(
                "world-state", "state",
                metadata={"snapshot": True, "rollback": True},
            ),
            object(),
        )
        self.plugins.mount(
            PluginDescriptor(
                "worldforge-policy", "model",
                metadata={
                    "owned": True,
                    "external_api": False,
                    "generation": self.policy_model.card.generation,
                },
            ),
            self.policy_model,
        )
        self.plugins.mount(
            PluginDescriptor(
                "adaptive-planner", "planner",
                dependencies=("world-state",),
                metadata={"fixed_dag": False},
            ),
            self.planner,
        )
        self.plugins.mount(
            PluginDescriptor(
                "counterfactual-brancher", "simulation",
                dependencies=("adaptive-planner",),
                metadata={"parallel": True, "canonical_isolation": True},
            ),
            self.brancher,
        )
        self.plugins.mount(
            PluginDescriptor(
                "state-verifier", "verification",
                dependencies=("world-state",),
                metadata={"side_effects": True},
            ),
            self.verifier,
        )
        self.plugins.mount(
            PluginDescriptor(
                "skill-bank", "skills",
                metadata={"evolutionary": True},
            ),
            self.skills,
        )
        self.plugins.mount(
            PluginDescriptor(
                "action-sandbox", "sandbox",
                dependencies=("world-state",),
                metadata={"pre_execution_guard": True},
            ),
            self.sandbox,
        )
        self.plugins.mount(
            PluginDescriptor(
                "recursive-scheduler", "subagents",
                dependencies=("adaptive-planner",),
                metadata={"state_conditioned": True, "advice_is_bounded": True},
            ),
            self.recursive,
        )
        self.plugins.mount(
            PluginDescriptor(
                "population-selfplay", "curriculum",
                dependencies=("skill-bank",),
                metadata={"profiles": 4},
            ),
            self.selfplay,
        )
        self.plugins.mount(
            PluginDescriptor(
                "failure-evolver", "evolution",
                dependencies=("skill-bank", "state-verifier"),
                metadata={"regression_gate": True},
            ),
            self.evolver,
        )

    async def _emit(
        self,
        session_id: str,
        event_type: str,
        payload: dict,
        sink: EventSink | None,
    ) -> RuntimeEvent:
        event = self.events.append(session_id, event_type, payload)
        if sink:
            await sink(event)
        return event

    async def run(
        self,
        config: RunConfig,
        *,
        session_id: str | None = None,
        sink: EventSink | None = None,
        demo_delay: float = .035,
        session_meta: dict | None = None,
    ) -> RunSummary:
        session_id = session_id or f"wf-{uuid.uuid4().hex[:10]}"
        scenario = get_scenario(config.scenario_id)
        goal = scenario.goal.model_copy(deep=True)
        goal.max_steps = config.max_steps
        env = BalanceLabEnv()
        state = env.reset(scenario, config.seed)

        # Each run gets isolated decision components. Shared mutable policy, skill,
        # memory and sandbox state must never leak across concurrent sessions.
        run_policy = self.policy_model.clone()
        run_skills = SkillBank()
        run_skills.skills = {
            key: value.model_copy(deep=True)
            for key, value in self.skills.skills.items()
        }
        run_memory = copy.deepcopy(self.memory)
        planner = AdaptivePlanner(run_skills, run_memory, run_policy)
        brancher = CounterfactualBrancher(planner, self.verifier)
        sandbox = ActionSandbox()
        episode_records: list[OutcomeRecord] = []

        started = time.time()
        summary = RunSummary(
            session_id=session_id,
            scenario_id=config.scenario_id,
            status="running",
            started_at=started,
        )
        self.events.create_session(
            session_id,
            meta={
                "config": config.model_dump(),
                "scenario": scenario.name,
                **(session_meta or {}),
            },
        )

        await self._emit(session_id, "run.started", {
            "scenario": scenario.model_dump(),
            "config": config.model_dump(),
            "plugins": self.plugins.describe(),
            "architecture": "world-state-native",
            "policy": run_policy.card_dict(),
            "product_mode": "game-autonomous-testing",
        }, sink)
        await self._emit(session_id, "world.state", {
            "state": state.model_dump(),
            "belief": planner.make_belief(state).model_dump(),
        }, sink)

        action_counter: Counter[str] = Counter()
        min_hp = state.player_hp
        rollback_used_at: set[int] = set()
        qa_probe_done = False
        policy_groups: list[PolicyGroup] = []

        for _ in range(goal.max_steps):
            if state.terminal:
                break

            decision_started = time.perf_counter()
            checkpoint = env.snapshot()
            events = self.events.list_events(session_id)
            checkpoint_seq = events[-1].seq if events else 0
            self.events.save_snapshot(session_id, checkpoint_seq, checkpoint)
            await self._emit(session_id, "checkpoint.created", {
                "tick": state.tick,
                "state": state.model_dump(),
            }, sink)

            if not qa_probe_done and "exploit-test" in state.tags:
                qa_probe_done = True
                probe = env.clone(seed_offset=991)
                probe_steps = []
                for probe_index in range(5):
                    legal_probe = probe.legal_actions(probe.state)
                    if ActionKind.FARM.value not in legal_probe or probe.state.terminal:
                        break
                    before_gold = probe.state.gold
                    probe_state, probe_reward, probe_done, probe_info = probe.step(
                        GameAction(
                            kind=ActionKind.FARM,
                            rationale="adversarial exploit probe",
                            source="runtime-probe",
                        )
                    )
                    probe_steps.append({
                        "step": probe_index + 1,
                        "reward": round(probe_reward, 4),
                        "gold_delta": probe_state.gold - before_gold,
                        "hp": probe_state.player_hp,
                        "events": probe_info.get("events", []),
                    })
                    if probe.anomalies or probe_done:
                        break
                if probe.anomalies:
                    await self._emit(session_id, "qa.finding", {
                        "tick": state.tick,
                        "source": "adversarial-probe",
                        "severity": "high",
                        "events": ["isolated_reward_loop_probe"],
                        "anomalies": probe.anomalies,
                        "canonical_untouched": env.state.model_dump() == state.model_dump(),
                        "evidence": {
                            "probe_steps": probe_steps,
                            "final_probe_state": probe.state.model_dump(),
                        },
                    }, sink)

            belief = planner.make_belief(state)
            active_skills = run_skills.active_for(state, belief.uncertainty)
            summary.skills_used += len(active_skills)

            specialist_bias: dict[str, float] = {}
            if config.enable_recursive_agents:
                tree = self.recursive.deliberate(state, belief, goal)
                specialist_bias = self.recursive.aggregate_bias(tree)
                await self._emit(session_id, "subagent.deliberation", {
                    "tick": state.tick,
                    "tree": tree.to_dict(),
                    "aggregate_bias": specialist_bias,
                }, sink)

            ranked = planner.rank(
                state,
                env.legal_actions(state),
                goal,
                extra_bias=specialist_bias,
            )
            policy_scores = run_policy.rank(
                state, belief, goal, env.legal_actions(state)
            )
            if policy_scores:
                policy_action = ActionKind(max(policy_scores, key=policy_scores.get))
                await self._emit(session_id, "policy.prior", {
                    "policy": run_policy.card.name,
                    "generation": self.policy_model.card.generation,
                    "action": policy_action.value,
                    "scores": policy_scores,
                    "confidence": run_policy.confidence(policy_scores),
                    "explanation": run_policy.explain(
                        state, belief, goal, policy_action
                    ),
                    "external_api": False,
                }, sink)

            await self._emit(session_id, "planner.candidates", {
                "tick": state.tick,
                "candidates": [action.value for action in ranked.candidates],
                "aggregate": ranked.aggregate,
                "active_skills": [skill.model_dump() for skill in active_skills],
                "council": [vote.model_dump() for vote in ranked.votes],
                "specialist_bias": specialist_bias,
                "planner_mode": "state-conditioned",
            }, sink)

            branches = []
            if config.enable_counterfactual and len(ranked.candidates) > 1:
                branch_state = state.model_copy(deep=True)
                branch_belief = belief.model_copy(deep=True)
                branches = brancher.evaluate(
                    env,
                    ranked.candidates,
                    goal,
                    width=config.branch_width,
                    horizon=config.rollout_horizon,
                    rollouts=config.rollouts_per_branch,
                )
                summary.branches_evaluated += len(branches)
                await self._emit(session_id, "counterfactual.evaluated", {
                    "tick": state.tick,
                    "branches": [branch.model_dump() for branch in branches],
                    "canonical_untouched": env.state.model_dump() == state.model_dump(),
                    "branch_width": config.branch_width,
                    "rollout_horizon": config.rollout_horizon,
                    "rollouts_per_branch": config.rollouts_per_branch,
                }, sink)

                reward_map: dict[str, float] = {}
                for branch in branches:
                    action_name = branch.first_action.value
                    reward_map[action_name] = max(
                        reward_map.get(action_name, float("-inf")),
                        float(branch.score),
                    )
                if len(reward_map) >= 2:
                    policy_groups.append(PolicyGroup(
                        state=branch_state,
                        belief=branch_belief,
                        goal=goal.model_copy(deep=True),
                        rewards=reward_map,
                    ))

                selected = branches[0].first_action
                second = branches[1].score if len(branches) > 1 else branches[0].score
                confidence = min(
                    .98,
                    .58 + max(0.0, branches[0].score - second) / 55,
                )
                rationale = f"verified branch utility={branches[0].score:.2f}"
            else:
                selected = ranked.candidates[0]
                values = sorted(ranked.aggregate.values(), reverse=True)
                margin = values[0] - values[1] if len(values) > 1 else values[0]
                confidence = min(.95, .55 + max(0, margin) / 15)
                rationale = f"planner utility={ranked.aggregate[selected.value]:.2f}"

            frame = DecisionFrame(
                tick=state.tick,
                candidates=ranked.candidates,
                votes=ranked.votes,
                branch_results=branches,
                selected=selected,
                rationale=rationale,
                confidence=confidence,
            )
            decision_payload = frame.model_dump()
            decision_payload["latency_ms"] = round(
                (time.perf_counter() - decision_started) * 1000, 2
            )
            decision_payload["decision_source"] = (
                "verified-counterfactual" if branches else "adaptive-planner"
            )
            await self._emit(
                session_id, "decision.committed", decision_payload, sink
            )

            sandbox_decision = sandbox.validate(
                selected, state, env.legal_actions(state)
            )
            await self._emit(session_id, "sandbox.checked", {
                "action": selected.value,
                **sandbox_decision.__dict__,
            }, sink)
            if not sandbox_decision.allowed:
                alternatives = [
                    action for action in ranked.candidates
                    if action != selected
                    and sandbox.validate(
                        action, state, env.legal_actions(state)
                    ).allowed
                ]
                if alternatives:
                    selected = alternatives[0]
                    await self._emit(session_id, "runtime.replan", {
                        "alternative": selected.value,
                        "reason": sandbox_decision.reason,
                        "pre_execution": True,
                    }, sink)

            before = state.model_copy(deep=True)
            action_counter[selected.value] += 1
            sandbox.record(selected)
            state, reward, done, info = env.step(GameAction(
                kind=selected,
                rationale=rationale,
                confidence=confidence,
                source="worldforge-runtime",
            ))
            verification = self.verifier.verify(
                before, state, info, goal, env.anomalies
            )
            await self._emit(session_id, "action.executed", {
                "action": selected.value,
                "reward": round(reward, 4),
                "state": state.model_dump(),
                "info": info,
                "verification": verification.__dict__,
            }, sink)

            if info.get("events") or env.anomalies:
                await self._emit(session_id, "qa.finding", {
                    "tick": state.tick,
                    "events": info.get("events", []),
                    "anomalies": env.anomalies,
                    "severity": "high" if env.anomalies else "info",
                    "evidence": {
                        "action": selected.value,
                        "state": state.model_dump(),
                    },
                }, sink)

            if (
                verification.recommendation == "rollback"
                and branches
                and state.tick not in rollback_used_at
            ):
                rollback_used_at.add(state.tick)
                env.restore(checkpoint)
                state = env.state.model_copy(deep=True)
                summary.recovery_events += 1
                await self._emit(session_id, "runtime.rollback", {
                    "reason": verification.violations,
                    "restored_state": state.model_dump(),
                    "replan": True,
                }, sink)
                alternatives = [
                    branch.first_action
                    for branch in branches
                    if branch.first_action != selected
                ]
                if alternatives:
                    alternative = alternatives[0]
                    before = state.model_copy(deep=True)
                    action_counter[alternative.value] += 1
                    state, reward, done, info = env.step(GameAction(
                        kind=alternative,
                        rationale="rollback alternative",
                        confidence=.7,
                        source="runtime-recovery",
                    ))
                    verification = self.verifier.verify(
                        before, state, info, goal, env.anomalies
                    )
                    await self._emit(session_id, "runtime.replan", {
                        "alternative": alternative.value,
                        "reward": round(reward, 4),
                        "state": state.model_dump(),
                        "verification": verification.__dict__,
                    }, sink)

            min_hp = min(min_hp, state.player_hp)
            summary.invalid_actions += 1 if info.get("invalid") else 0
            signature = self.memory.signature(before)
            record = OutcomeRecord(
                config.scenario_id,
                signature,
                state.last_action or selected.value,
                reward,
                bool(state.outcome == "victory"),
            )
            run_memory.add(record)
            episode_records.append(record)
            await self._emit(session_id, "world.state", {
                "state": state.model_dump(),
                "belief": planner.make_belief(state).model_dump(),
                "anomalies": env.anomalies,
            }, sink)
            if demo_delay:
                await asyncio.sleep(demo_delay)

        if not state.terminal:
            state.terminal = True
            state.outcome = "timeout"
            state.score -= 15
            await self._emit(
                session_id, "run.timeout", {"state": state.model_dump()}, sink
            )

        evolved = False
        human_feedback_gate = bool((session_meta or {}).get("human_feedback_gate", True))
        # Policy / Skill / global Memory are shared learning state. Commit those
        # updates in one short critical section while keeping the expensive run
        # itself fully concurrent.
        async with self._evolution_lock:
            for record in episode_records:
                self.memory.add(record)

            if config.enable_evolution:
                signal = self.evolver.attribute(
                    outcome=state.outcome,
                    min_hp=min_hp,
                    farm_count=action_counter[ActionKind.FARM.value],
                    invalid_actions=summary.invalid_actions,
                    last_action=state.last_action,
                )
                if signal:
                    await self._emit(
                        session_id, "evolution.attributed", signal.__dict__, sink
                    )
                    patch = self.evolver.evolve(
                        signal,
                        lambda candidate: self._regression_eval(
                            config.scenario_id, candidate, self.policy_model
                        ),
                        human_approved=human_feedback_gate,
                    )
                    evolved = patch.accepted
                    await self._emit(
                        session_id, "evolution.patch", patch.model_dump(), sink
                    )

                if policy_groups:
                    candidate_policy, optimization = self.policy_optimizer.optimize(
                        self.policy_model, policy_groups
                    )
                    baseline = self._regression_eval(
                        config.scenario_id, None, self.policy_model
                    )
                    candidate_score = self._regression_eval(
                        config.scenario_id, None, candidate_policy
                    )
                    policy_accepted = (
                        human_feedback_gate
                        and optimization["updates"] > 0
                        and candidate_score >= baseline + .001
                        and optimization["mean_kl"] <= self.policy_optimizer.kl_limit
                    )
                    if policy_accepted:
                        self.policy_model = candidate_policy
                        self.policy_model.save(self.policy_path)
                        self.planner.policy_model = self.policy_model
                        self.plugins.mount(
                            PluginDescriptor(
                                "worldforge-policy",
                                "model",
                                metadata={
                                    "owned": True,
                                    "external_api": False,
                                    "generation": self.policy_model.card.generation,
                                },
                            ),
                            self.policy_model,
                        )
                        evolved = True
                    await self._emit(session_id, "policy.optimization", {
                        **optimization,
                        "regression_before": round(baseline, 4),
                        "regression_after": round(candidate_score, 4),
                        "accepted": policy_accepted,
                        "human_feedback_gate": human_feedback_gate,
                        "generation": (
                            self.policy_model.card.generation
                            if policy_accepted
                            else candidate_policy.card.generation
                        ),
                    }, sink)

        summary.status = "completed"
        summary.outcome = state.outcome
        summary.steps = state.tick
        summary.score = round(state.score, 4)
        summary.evolved = evolved
        summary.finished_at = time.time()
        await self._emit(session_id, "run.completed", {
            "summary": summary.model_dump(),
            "final_state": state.model_dump(),
            "skills": self.skills.snapshot(),
            "event_chain_valid": self.events.verify_chain(session_id),
            "policy": self.policy_model.card_dict(),
            "findings": env.anomalies,
        }, sink)
        return summary

    def _regression_eval(
        self,
        scenario_id: str,
        candidate: Skill | None,
        policy: WorldForgePolicy,
    ) -> float:
        """Deterministic replay gate for skill and policy changes."""
        bank = SkillBank()
        bank.skills = {
            key: value.model_copy(deep=True)
            for key, value in self.skills.skills.items()
        }
        if candidate is not None:
            patched = candidate.model_copy(deep=True)
            patched.status = "active"
            bank.skills[patched.skill_id] = patched

        planner = AdaptivePlanner(bank, EpisodicMemory(), policy)
        scores = []
        for seed in (11, 23, 37, 51):
            spec = get_scenario(scenario_id)
            env = BalanceLabEnv()
            state = env.reset(spec, seed)
            goal = spec.goal
            for _ in range(min(goal.max_steps, 14)):
                ranked = planner.rank(state, env.legal_actions(state), goal)
                state, _, done, _ = env.step(
                    GameAction(kind=ranked.candidates[0], source="regression-gate")
                )
                if done:
                    break
            success = 1.0 if state.outcome == "victory" else 0.0
            health = max(0, state.player_hp) / max(1, state.player_max_hp)
            progress = 1 - state.enemy_hp / max(1, state.enemy_max_hp)
            scores.append(success * .62 + health * .16 + progress * .22)
        return sum(scores) / len(scores)
