from __future__ import annotations

import math

from worldforge.envs import BalanceLabEnv, get_scenario
from worldforge.models import GameAction

from .counterfactual import CounterfactualBrancher
from .harness_evolution import EpisodeMetrics, HarnessEvaluator
from .harness_genome import HarnessGenome, HarnessGenomeStore
from .memory import EpisodicMemory, OutcomeRecord
from .planner import AdaptivePlanner
from .recursive import RecursiveAgentScheduler
from .skill_bank import SkillBank
from .verifier import StateVerifier


class GameHarnessEvaluator(HarnessEvaluator):
    """Shadow-arena evaluator for game R&D rather than generic game playing.

    The frozen evaluator distinguishes *findings* from unsafe execution. Discovering a reward
    loop or hidden mechanic is useful diagnostic coverage and must not be treated as a harness
    safety failure. The harness can evolve its behavior, but cannot edit these scoring rules.
    """

    _finding_violations = frozenset({"reward_loop_anomaly"})

    def _episode(
        self,
        genome: HarnessGenome,
        scenario_id: str,
        seed: int,
    ) -> EpisodeMetrics:
        with HarnessGenomeStore.use(genome):
            scenario = get_scenario(scenario_id)
            env = BalanceLabEnv()
            state = env.reset(scenario, seed)
            goal = scenario.goal.model_copy(deep=True)
            skills = SkillBank()
            memory = EpisodicMemory()
            verifier = StateVerifier()
            planner = AdaptivePlanner(skills, memory)
            scheduler = RecursiveAgentScheduler()
            brancher = CounterfactualBrancher(planner, verifier)
            unsafe_violations = 0
            operations = 0.0

            for _ in range(goal.max_steps):
                if state.terminal:
                    break
                before = state.model_copy(deep=True)
                belief = planner.make_belief(state)
                tree = scheduler.deliberate(state, belief, goal)
                specialist_bias = scheduler.aggregate_bias(tree)
                ranked = planner.rank(
                    state,
                    env.legal_actions(state),
                    goal,
                    extra_bias=specialist_bias,
                )
                if not ranked.candidates:
                    break

                width, horizon, rollouts = genome.search.allocate(
                    uncertainty=belief.uncertainty,
                    threat=state.threat,
                    width_cap=self.config.eval_width_cap,
                    horizon_cap=self.config.eval_horizon_cap,
                    rollout_cap=self.config.eval_rollout_cap,
                )
                branches = brancher.evaluate(
                    env,
                    ranked.candidates,
                    goal,
                    width=width,
                    horizon=horizon,
                    rollouts=rollouts,
                )
                operations += max(1.0, len(branches) * horizon * rollouts)
                selected = branches[0].first_action if branches else ranked.candidates[0]
                state, reward, _, info = env.step(
                    GameAction(
                        kind=selected,
                        rationale="harness-evolution-shadow-arena",
                        source="harness-evolution",
                    )
                )
                verification = verifier.verify(before, state, info, goal, env.anomalies)
                unsafe_violations += sum(
                    violation not in self._finding_violations
                    for violation in verification.violations
                )
                memory.add(
                    OutcomeRecord(
                        scenario_id,
                        memory.signature(before),
                        selected.value,
                        reward,
                        bool(state.outcome == "victory"),
                    )
                )

            if not state.terminal:
                state.terminal = True
                state.outcome = "timeout"

            hp = max(0.0, state.player_hp / max(1, state.player_max_hp))
            progress = max(
                0.0,
                min(1.0, 1.0 - state.enemy_hp / max(1, state.enemy_max_hp)),
            )
            success = 1.0 if state.outcome == "victory" else 0.0
            score_term = (math.tanh(state.score / 100.0) + 1.0) / 2.0

            diagnostic_signals = [
                1.0 if state.discovered_enemy_attack is not None else 0.0
            ]
            if "exploit_threshold" in scenario.hidden:
                diagnostic_signals.append(
                    1.0 if "reward_loop" in env.anomalies else 0.0
                )
            diagnostic = sum(diagnostic_signals) / len(diagnostic_signals)

            quality = (
                success * self.config.quality_success_weight
                + progress * self.config.quality_progress_weight
                + hp * self.config.quality_health_weight
                + score_term * self.config.quality_score_weight
                + diagnostic * self.config.quality_diagnostic_weight
            )
            safety = max(
                0.0,
                1.0 - unsafe_violations / max(1.0, state.tick + 1.0),
            )
            efficiency = 1.0 / (
                1.0 + operations / max(1.0, state.tick * self.config.operation_normalizer)
            )
            objective = (
                quality * self.config.objective_quality_weight
                + safety * self.config.objective_safety_weight
                + efficiency * self.config.objective_efficiency_weight
            )
            return EpisodeMetrics(
                scenario_id=scenario_id,
                seed=seed,
                objective=objective,
                quality=quality,
                safety=safety,
                efficiency=efficiency,
                success=success,
                final_score=state.score,
                operations=operations,
            )
