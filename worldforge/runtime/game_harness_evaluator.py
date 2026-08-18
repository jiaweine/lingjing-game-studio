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
    """Frozen shadow-arena evaluator for game R&D rather than generic game playing.

    The judge is anomaly-agnostic: findings improve diagnostic coverage, while verifier states
    that require rollback/replan (or violate critical invariants) count against safety. The
    harness can evolve its behavior but cannot edit this credit protocol.
    """

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
            finding_events = 0
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
                if verification.recommendation in {"rollback", "replan"} or verification.severity == "critical":
                    unsafe_violations += len(verification.violations)
                elif verification.violations:
                    finding_events += len(verification.violations)
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
            observed_signal = 1.0 if state.discovered_enemy_attack is not None else 0.0
            finding_signal = 1.0 if env.anomalies or finding_events else 0.0
            diagnostic = (observed_signal + finding_signal) / 2.0

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
