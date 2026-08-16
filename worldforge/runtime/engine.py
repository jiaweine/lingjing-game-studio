from __future__ import annotations

import asyncio
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
from .recursive import RecursiveAgentScheduler
from .sandbox import ActionSandbox
from .selfplay import PopulationSelfPlay
from .skill_bank import SkillBank
from .verifier import StateVerifier
from .worldforge_model import WorldForgeM1

EventSink = Callable[[RuntimeEvent], Awaitable[None]]


class WorldForgeEngine:
    """World-state-native autonomous game runtime.

    The canonical environment is kept separate from speculative branches. The owned decision
    kernel combines deterministic council scores, memory, the local WorldForge policy prior,
    state-conditioned specialist tasks and counterfactual rollouts. Side effects are guarded before
    execution and verified afterwards; a rejected or rolled-back action is never allowed to pollute
    canonical statistics.
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        skill_bank: SkillBank | None = None,
        memory: EpisodicMemory | None = None,
    ) -> None:
        self.events = EventStore(db_path)
        self.skills = skill_bank or SkillBank()
        self.memory = memory or EpisodicMemory()
        self.verifier = StateVerifier()
        model_path = Path(__file__).resolve().parents[2] / "models" / "worldforge_m1.json"
        self.policy_model = WorldForgeM1.load_or_bootstrap(model_path)
        self.planner = AdaptivePlanner(self.skills, self.memory, self.policy_model)
        self.brancher = CounterfactualBrancher(self.planner, self.verifier)
        self.evolver = FailureDrivenEvolver(self.skills)
        # This instance is a policy template only. Every run creates its own sandbox state.
        self.sandbox = ActionSandbox()
        self.selfplay = PopulationSelfPlay(self.skills)
        self.recursive = RecursiveAgentScheduler()
        self.plugins = PluginRegistry()
        self._mount_defaults()

    def _mount_defaults(self) -> None:
        self.plugins.mount(
            PluginDescriptor("world-state", "state", metadata={"snapshot": True, "rollback": True}), object()
        )
        self.plugins.mount(
            PluginDescriptor(
                "worldforge-m1",
                "model",
                metadata={"owned": True, "external_api": False, "version": self.policy_model.card.version},
            ),
            self.policy_model,
        )
        self.plugins.mount(
            PluginDescriptor("adaptive-planner", "planner", dependencies=("world-state",), metadata={"fixed_dag": False}),
            self.planner,
        )
        self.plugins.mount(
            PluginDescriptor(
                "counterfactual-brancher", "simulation", dependencies=("adaptive-planner",), metadata={"parallel": True}
            ),
            self.brancher,
        )
        self.plugins.mount(
            PluginDescriptor("state-verifier", "verification", dependencies=("world-state",), metadata={"side_effects": True}),
            self.verifier,
        )
        self.plugins.mount(PluginDescriptor("skill-bank", "skills", metadata={"versioned": True}), self.skills)
        self.plugins.mount(
            PluginDescriptor(
                "action-sandbox", "sandbox", dependencies=("world-state",), metadata={"pre_execution_guard": True, "session_scoped": True}
            ),
            self.sandbox,
        )
        self.plugins.mount(
            PluginDescriptor(
                "recursive-scheduler",
                "subagents",
                dependencies=("adaptive-planner",),
                metadata={"state_conditioned": True, "deterministic_ids": True, "dynamic_spawn": True},
            ),
            self.recursive,
        )
        self.plugins.mount(
            PluginDescriptor("population-selfplay", "curriculum", dependencies=("skill-bank",), metadata={"profiles": 4}),
            self.selfplay,
        )
        self.plugins.mount(
            PluginDescriptor(
                "failure-evolver", "evolution", dependencies=("skill-bank", "state-verifier"), metadata={"regression_gate": True}
            ),
            self.evolver,
        )

    async def _emit(
        self, session_id: str, event_type: str, payload: dict, sink: EventSink | None
    ) -> RuntimeEvent:
        ev = self.events.append(session_id, event_type, payload)
        if sink:
            await sink(ev)
        return ev

    async def run(
        self,
        config: RunConfig,
        *,
        session_id: str | None = None,
        sink: EventSink | None = None,
        demo_delay: float = 0.035,
    ) -> RunSummary:
        session_id = session_id or f"wf-{uuid.uuid4().hex[:10]}"
        scenario = get_scenario(config.scenario_id)
        goal = scenario.goal.model_copy(deep=True)
        goal.max_steps = config.max_steps
        env = BalanceLabEnv()
        state = env.reset(scenario, config.seed)
        sandbox = ActionSandbox(self.sandbox.max_irreversible_per_window)
        started = time.time()
        summary = RunSummary(
            session_id=session_id,
            scenario_id=config.scenario_id,
            status="running",
            started_at=started,
        )
        self.events.create_session(
            session_id, meta={"config": config.model_dump(), "scenario": scenario.name}
        )

        await self._emit(
            session_id,
            "run.started",
            {
                "scenario": scenario.model_dump(),
                "config": config.model_dump(),
                "plugins": self.plugins.describe(),
                "architecture": "world-state-native",
                "model": self.policy_model.card_dict(),
                "product_mode": "game-ai-autonomous-testing",
            },
            sink,
        )
        await self._emit(
            session_id,
            "world.state",
            {"state": state.model_dump(), "belief": self.planner.make_belief(state).model_dump()},
            sink,
        )

        action_counter: Counter[str] = Counter()
        min_hp = state.player_hp
        rollback_used_at: set[int] = set()
        qa_probe_done = False

        for _ in range(goal.max_steps):
            if state.terminal:
                break
            decision_tick = state.tick
            decision_started = time.perf_counter()
            checkpoint = env.snapshot()
            events = self.events.list_events(session_id)
            checkpoint_seq = events[-1].seq if events else 0
            self.events.save_snapshot(session_id, checkpoint_seq, checkpoint)
            await self._emit(
                session_id,
                "checkpoint.created",
                {"tick": state.tick, "state": state.model_dump()},
                sink,
            )

            if not qa_probe_done and "exploit-test" in state.tags:
                qa_probe_done = True
                probe = env.clone(seed_offset=991)
                probe_steps = []
                for probe_i in range(5):
                    legal_probe = probe.legal_actions(probe.state)
                    if ActionKind.FARM.value not in legal_probe or probe.state.terminal:
                        break
                    before_gold = probe.state.gold
                    p_state, p_reward, p_done, p_info = probe.step(
                        GameAction(kind=ActionKind.FARM, rationale="adversarial exploit probe")
                    )
                    probe_steps.append(
                        {
                            "step": probe_i + 1,
                            "reward": round(p_reward, 4),
                            "gold_delta": p_state.gold - before_gold,
                            "hp": p_state.player_hp,
                            "events": p_info.get("events", []),
                        }
                    )
                    if probe.anomalies or p_done:
                        break
                if probe.anomalies:
                    await self._emit(
                        session_id,
                        "qa.finding",
                        {
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
                        },
                        sink,
                    )

            belief = self.planner.make_belief(state)
            active_skills = self.skills.active_for(state, belief.uncertainty)
            summary.skills_used += len(active_skills)
            legal_actions = env.legal_actions(state)
            ranked = self.planner.rank(state, legal_actions, goal)
            if config.enable_recursive_agents:
                tree = await self.recursive.analyze(
                    state, belief, goal, session_id=session_id, tick=decision_tick
                )
                await self._emit(
                    session_id,
                    "subagent.tree",
                    {"tick": state.tick, "tree": tree.to_dict()},
                    sink,
                )

            model_scores = self.policy_model.rank(state, belief, goal, legal_actions)
            if model_scores:
                model_action = ActionKind(max(model_scores, key=model_scores.get))
                await self._emit(
                    session_id,
                    "model.policy",
                    {
                        "model": self.policy_model.card.name,
                        "version": self.policy_model.card.version,
                        "action": model_action.value,
                        "scores": model_scores,
                        "confidence": self.policy_model.confidence(model_scores),
                        "explanation": self.policy_model.explain(state, belief, goal, model_action),
                        "external_api": False,
                    },
                    sink,
                )
            await self._emit(
                session_id,
                "planner.candidates",
                {
                    "tick": state.tick,
                    "candidates": [a.value for a in ranked.candidates],
                    "aggregate": ranked.aggregate,
                    "active_skills": [s.model_dump() for s in active_skills],
                    "council": [v.model_dump() for v in ranked.votes],
                    "planner_mode": "state-conditioned",
                },
                sink,
            )

            branches = []
            if config.enable_counterfactual and len(ranked.candidates) > 1:
                branches = self.brancher.evaluate(
                    env,
                    ranked.candidates,
                    goal,
                    width=config.branch_width,
                    horizon=config.rollout_horizon,
                    rollouts=config.rollouts_per_branch,
                )
                summary.branches_evaluated += len(branches)
                await self._emit(
                    session_id,
                    "counterfactual.evaluated",
                    {
                        "tick": state.tick,
                        "branches": [b.model_dump() for b in branches],
                        "canonical_untouched": env.state.model_dump() == state.model_dump(),
                        "branch_width": config.branch_width,
                        "rollout_horizon": config.rollout_horizon,
                        "rollouts_per_branch": config.rollouts_per_branch,
                    },
                    sink,
                )
                selected = branches[0].first_action
                runner_up = branches[1].score if len(branches) > 1 else 0.0
                confidence = min(0.98, 0.58 + max(0.0, branches[0].score - runner_up) / 55)
                rationale = f"counterfactual best branch score={branches[0].score:.2f}"
            else:
                selected = ranked.candidates[0]
                vals = sorted(ranked.aggregate.values(), reverse=True)
                margin = vals[0] - vals[1] if len(vals) > 1 else vals[0]
                confidence = min(0.95, 0.55 + max(0, margin) / 15)
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
            await self._emit(session_id, "decision.committed", decision_payload, sink)

            sandbox_decision = sandbox.validate(selected, state, legal_actions)
            await self._emit(
                session_id,
                "sandbox.checked",
                {"action": selected.value, **sandbox_decision.__dict__},
                sink,
            )
            if not sandbox_decision.allowed:
                alternatives = [
                    a
                    for a in ranked.candidates
                    if a != selected and sandbox.validate(a, state, legal_actions).allowed
                ]
                if not alternatives:
                    await self._emit(
                        session_id,
                        "runtime.blocked",
                        {
                            "tick": decision_tick,
                            "action": selected.value,
                            "reason": sandbox_decision.reason,
                            "canonical_untouched": env.state.model_dump() == state.model_dump(),
                        },
                        sink,
                    )
                    continue
                selected = alternatives[0]
                rationale = f"sandbox replan after {sandbox_decision.reason}"
                await self._emit(
                    session_id,
                    "runtime.replan",
                    {
                        "alternative": selected.value,
                        "reason": sandbox_decision.reason,
                        "pre_execution": True,
                    },
                    sink,
                )

            before = state.model_copy(deep=True)
            sandbox.record(selected)
            action_counter[selected.value] += 1
            state, reward, done, info = env.step(
                GameAction(kind=selected, rationale=rationale, confidence=confidence)
            )
            verification = self.verifier.verify(before, state, info, goal, env.anomalies)
            await self._emit(
                session_id,
                "action.executed",
                {
                    "action": selected.value,
                    "reward": round(reward, 4),
                    "state": state.model_dump(),
                    "info": info,
                    "verification": verification.__dict__,
                },
                sink,
            )
            if info.get("events") or env.anomalies:
                await self._emit(
                    session_id,
                    "qa.finding",
                    {
                        "tick": state.tick,
                        "events": info.get("events", []),
                        "anomalies": env.anomalies,
                        "severity": "high" if env.anomalies else "info",
                        "evidence": {"action": selected.value, "state": state.model_dump()},
                    },
                    sink,
                )

            accepted_action: ActionKind | None = selected
            accepted_before = before
            recovery_needed = verification.recommendation in {"rollback", "replan"}
            if recovery_needed and branches and decision_tick not in rollback_used_at:
                rollback_used_at.add(decision_tick)
                # A reverted action must not consume budget or bias future evolution/memory.
                sandbox.undo(selected)
                action_counter[selected.value] -= 1
                if action_counter[selected.value] <= 0:
                    del action_counter[selected.value]
                env.restore(checkpoint)
                state = env.state.model_copy(deep=True)
                accepted_action = None
                summary.recovery_events += 1
                await self._emit(
                    session_id,
                    "runtime.rollback",
                    {
                        "reason": verification.violations,
                        "restored_state": state.model_dump(),
                        "replan": True,
                        "reverted_action": selected.value,
                    },
                    sink,
                )

                legal_after_restore = env.legal_actions(state)
                alternatives = [
                    b.first_action
                    for b in branches
                    if b.first_action != selected
                    and sandbox.validate(b.first_action, state, legal_after_restore).allowed
                ]
                for alternative in alternatives:
                    alt_before = state.model_copy(deep=True)
                    sandbox.record(alternative)
                    action_counter[alternative.value] += 1
                    alt_state, alt_reward, alt_done, alt_info = env.step(
                        GameAction(
                            kind=alternative,
                            rationale="verified rollback alternative",
                            confidence=0.7,
                        )
                    )
                    alt_verification = self.verifier.verify(
                        alt_before, alt_state, alt_info, goal, env.anomalies
                    )
                    await self._emit(
                        session_id,
                        "runtime.replan",
                        {
                            "alternative": alternative.value,
                            "reward": round(alt_reward, 4),
                            "state": alt_state.model_dump(),
                            "verification": alt_verification.__dict__,
                            "sandbox_checked": True,
                        },
                        sink,
                    )
                    if alt_verification.recommendation == "rollback":
                        sandbox.undo(alternative)
                        action_counter[alternative.value] -= 1
                        if action_counter[alternative.value] <= 0:
                            del action_counter[alternative.value]
                        env.restore(checkpoint)
                        state = env.state.model_copy(deep=True)
                        await self._emit(
                            session_id,
                            "runtime.rollback",
                            {
                                "reason": alt_verification.violations,
                                "restored_state": state.model_dump(),
                                "replan": False,
                                "reverted_action": alternative.value,
                                "post_replan_rejection": True,
                            },
                            sink,
                        )
                        continue
                    state, reward, done, info, verification = (
                        alt_state,
                        alt_reward,
                        alt_done,
                        alt_info,
                        alt_verification,
                    )
                    selected = alternative
                    accepted_action = alternative
                    accepted_before = alt_before
                    break

            if accepted_action is None:
                await self._emit(
                    session_id,
                    "world.state",
                    {
                        "state": state.model_dump(),
                        "belief": self.planner.make_belief(state).model_dump(),
                        "anomalies": env.anomalies,
                        "recovered_without_commit": True,
                    },
                    sink,
                )
                if demo_delay:
                    await asyncio.sleep(demo_delay)
                continue

            min_hp = min(min_hp, state.player_hp)
            summary.invalid_actions += 1 if info.get("invalid") else 0
            signature = self.memory.signature(accepted_before)
            self.memory.add(
                OutcomeRecord(
                    config.scenario_id,
                    signature,
                    state.last_action or accepted_action.value,
                    reward,
                    bool(state.outcome == "victory"),
                )
            )
            await self._emit(
                session_id,
                "world.state",
                {
                    "state": state.model_dump(),
                    "belief": self.planner.make_belief(state).model_dump(),
                    "anomalies": env.anomalies,
                },
                sink,
            )
            if demo_delay:
                await asyncio.sleep(demo_delay)

        if not state.terminal:
            state.terminal = True
            state.outcome = "timeout"
            state.score -= 15
            await self._emit(session_id, "run.timeout", {"state": state.model_dump()}, sink)

        evolved = False
        if config.enable_evolution:
            signal = self.evolver.attribute(
                outcome=state.outcome,
                min_hp=min_hp,
                farm_count=action_counter[ActionKind.FARM.value],
                invalid_actions=summary.invalid_actions,
                last_action=state.last_action,
            )
            if signal:
                await self._emit(session_id, "evolution.attributed", signal.__dict__, sink)
                patch = self.evolver.evolve(
                    signal, lambda candidate: self._regression_eval(config.scenario_id, candidate)
                )
                evolved = patch.accepted
                await self._emit(session_id, "evolution.patch", patch.model_dump(), sink)

        summary.status = "completed"
        summary.outcome = state.outcome
        summary.steps = state.tick
        summary.score = round(state.score, 4)
        summary.evolved = evolved
        summary.finished_at = time.time()
        await self._emit(
            session_id,
            "run.completed",
            {
                "summary": summary.model_dump(),
                "final_state": state.model_dump(),
                "skills": self.skills.snapshot(),
                "event_chain_valid": self.events.verify_chain(session_id),
                "model": self.policy_model.card_dict(),
                "findings": env.anomalies,
                "committed_action_counts": dict(action_counter),
            },
            sink,
        )
        return summary

    def _regression_eval(self, scenario_id: str, candidate: Skill | None) -> float:
        """Small deterministic replay gate. It is model-free and side-effect-free."""
        bank = SkillBank()
        bank.skills = {k: v.model_copy(deep=True) for k, v in self.skills.skills.items()}
        if candidate is not None:
            c = candidate.model_copy(deep=True)
            c.status = "active"
            bank.skills[c.skill_id] = c
        planner = AdaptivePlanner(bank, EpisodicMemory(), self.policy_model)
        scores = []
        for seed in (11, 23, 37, 51):
            spec = get_scenario(scenario_id)
            env = BalanceLabEnv()
            state = env.reset(spec, seed)
            goal = spec.goal
            for _ in range(min(goal.max_steps, 14)):
                ranked = planner.rank(state, env.legal_actions(state), goal)
                state, reward, done, info = env.step(GameAction(kind=ranked.candidates[0]))
                if done:
                    break
            success = 1.0 if state.outcome == "victory" else 0.0
            health = max(0, state.player_hp) / max(1, state.player_max_hp)
            progress = 1 - state.enemy_hp / max(1, state.enemy_max_hp)
            scores.append(success * 0.62 + health * 0.16 + progress * 0.22)
        return sum(scores) / len(scores)
