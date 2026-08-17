from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import statistics
import uuid

from worldforge.models import BranchResult, GameAction

from .harness_genome import HarnessGenomeStore


class CounterfactualBrancher:
    """Counterfactual search whose budget and risk aggregation are genome-controlled.

    The caller supplies only hard resource caps. The active harness decides how much of that
    budget to spend for the current uncertainty/threat state.
    """

    def __init__(self, planner, verifier):
        self.planner = planner
        self.verifier = verifier

    def evaluate(self, env, candidates, goal, width=4, horizon=3, rollouts=3):
        gene = HarnessGenomeStore.current().search
        belief = self.planner.make_belief(env.state)
        width, horizon, rollouts = gene.allocate(
            uncertainty=belief.uncertainty,
            threat=env.state.threat,
            width_cap=width,
            horizon_cap=horizon,
            rollout_cap=rollouts,
        )
        selected = candidates[:width]
        if not selected:
            return []

        def run_action(action, branch_idx):
            scores: list[float] = []
            survivals: list[float] = []
            successes: list[float] = []
            all_violations: set[str] = set()
            final = None
            outcome = None
            terminal = False
            sample_actions: list[str] = []

            for rollout in range(rollouts):
                sim = env.clone(
                    seed_offset=0 if rollout == 0 else branch_idx * 101 + rollout
                )
                total = 0.0
                rollout_actions: list[str] = []
                rollout_violations: set[str] = set()
                act = action

                for _ in range(horizon):
                    before = sim.state.model_copy(deep=True)
                    after, reward, terminal, info = sim.step(
                        GameAction(kind=act, rationale="counterfactual")
                    )
                    verification = self.verifier.verify(
                        before,
                        after,
                        info,
                        goal,
                        getattr(sim, "anomalies", []),
                    )
                    total += reward
                    rollout_actions.append(act.value)
                    rollout_violations.update(verification.violations)
                    final = after
                    outcome = after.outcome
                    if terminal:
                        break
                    ranked = self.planner.rank(
                        after,
                        sim.legal_actions(after),
                        goal,
                    )
                    if not ranked.candidates:
                        break
                    act = ranked.candidates[0]

                score = self.verifier.branch_score(
                    final,
                    total,
                    goal,
                    sorted(rollout_violations),
                )
                scores.append(score)
                all_violations.update(rollout_violations)
                survivals.append(
                    max(0.0, final.player_hp / max(1, final.player_max_hp))
                )
                successes.append(1.0 if final.outcome == "victory" else 0.0)
                if not sample_actions:
                    sample_actions = rollout_actions

            mean = statistics.mean(scores)
            downside = min(scores)
            dispersion = statistics.pstdev(scores) if len(scores) > 1 else 0.0
            success_probability = statistics.mean(successes)
            active = HarnessGenomeStore.current().search
            risk_adjusted = (
                active.mean_weight * mean
                - active.dispersion_penalty * dispersion
                + active.downside_weight * downside
                + active.success_bonus * success_probability
            )
            return BranchResult(
                branch_id=f"b-{uuid.uuid4().hex[:8]}",
                first_action=action,
                rollout_actions=sample_actions,
                score=round(risk_adjusted, 4),
                expected_score=round(mean, 4),
                downside_score=round(downside, 4),
                success_probability=round(success_probability, 4),
                survival=round(statistics.mean(survivals), 4),
                terminal=terminal,
                outcome=outcome,
                violations=sorted(all_violations),
                final_state=final,
            )

        with ThreadPoolExecutor(max_workers=max(1, len(selected))) as executor:
            futures = [
                executor.submit(run_action, action, index)
                for index, action in enumerate(selected)
            ]
            results = [future.result() for future in futures]
        return sorted(results, key=lambda branch: branch.score, reverse=True)
