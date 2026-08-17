from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import statistics

from worldforge.envs import BalanceLabEnv, get_scenario, list_scenarios
from worldforge.models import ActionKind, BenchmarkRow, GameAction
from worldforge.runtime import (
    AdaptivePlanner, CounterfactualBrancher, EpisodicMemory, SkillBank, StateVerifier,
)
from worldforge.runtime.policy import WorldForgePolicy


@dataclass
class EpisodeResult:
    success: bool
    score: float
    steps: int
    invalid: int
    recovery: int
    decision_ops: int


def _components():
    skills = SkillBank()
    memory = EpisodicMemory()
    verifier = StateVerifier()
    model_path = Path.cwd() / "outputs" / "benchmark_policy.json"
    policy = WorldForgePolicy.load_or_bootstrap(model_path)
    planner = AdaptivePlanner(skills, memory, policy)
    return policy, verifier, planner, CounterfactualBrancher(planner, verifier)


def run_episode(harness, scenario_id, seed):
    spec = get_scenario(scenario_id)
    env = BalanceLabEnv()
    state = env.reset(spec, seed)
    policy, verifier, planner, brancher = _components()
    invalid = recovery = decision_ops = 0

    for _ in range(spec.goal.max_steps):
        if state.terminal:
            break
        legal = env.legal_actions(state)
        before_snapshot = env.snapshot()
        belief = planner.make_belief(state)
        branches = []

        if harness == "Policy":
            scores = policy.rank(state, belief, spec.goal, legal)
            action = ActionKind(max(scores, key=scores.get))
            decision_ops += len(scores)
        elif harness == "Policy + Planner":
            ranked = planner.rank(state, legal, spec.goal)
            action = ranked.candidates[0]
            decision_ops += len(ranked.candidates)
        elif harness == "Policy + Verification":
            ranked = planner.rank(state, legal, spec.goal)
            action = ranked.candidates[0]
            decision_ops += len(ranked.candidates)
        elif harness == "WorldForge Runtime":
            ranked = planner.rank(state, legal, spec.goal)
            branches = brancher.evaluate(
                env, ranked.candidates, spec.goal,
                width=4, horizon=3, rollouts=2,
            )
            action = branches[0].first_action if branches else ranked.candidates[0]
            decision_ops += len(ranked.candidates) + len(branches) * 3 * 2
        else:
            raise ValueError(harness)

        before = state.model_copy(deep=True)
        state, _, done, info = env.step(GameAction(kind=action))
        invalid += int(info.get("invalid", False))
        verification = verifier.verify(
            before, state, info, spec.goal, env.anomalies
        )
        if (
            harness in {"Policy + Verification", "WorldForge Runtime"}
            and verification.recommendation == "rollback"
        ):
            env.restore(before_snapshot)
            state = env.state.model_copy(deep=True)
            recovery += 1
            ranked = planner.rank(
                state, env.legal_actions(state), spec.goal
            )
            alternatives = (
                [branch.first_action for branch in branches if branch.first_action != action]
                if branches
                else [candidate for candidate in ranked.candidates if candidate != action]
            )
            alternative = alternatives[0] if alternatives else ranked.candidates[0]
            state, _, done, _ = env.step(GameAction(kind=alternative))
        if done:
            break

    return EpisodeResult(
        state.outcome == "victory",
        state.score,
        state.tick,
        invalid,
        recovery,
        decision_ops,
    )


def run_benchmark(seeds=24, scenarios=None):
    scenario_ids = scenarios or [
        scenario.scenario_id for scenario in list_scenarios()
    ]
    rows = []
    for harness in [
        "Policy",
        "Policy + Planner",
        "Policy + Verification",
        "WorldForge Runtime",
    ]:
        results = [
            run_episode(harness, scenario, 1000 + i*31 + j*7)
            for j, scenario in enumerate(scenario_ids)
            for i in range(seeds)
        ]
        successes = sum(result.success for result in results)
        failures = len(results) - successes
        recoveries = sum(result.recovery for result in results)
        rows.append(BenchmarkRow(
            harness=harness,
            success_rate=round(successes / len(results), 4),
            avg_score=round(statistics.mean(result.score for result in results), 3),
            avg_steps=round(statistics.mean(result.steps for result in results), 3),
            invalid_action_rate=round(
                sum(result.invalid for result in results)
                / max(1, sum(result.steps for result in results)),
                4,
            ),
            recovery_rate=round(
                recoveries / max(1, failures + recoveries), 4
            ),
            avg_decision_ops=round(
                statistics.mean(result.decision_ops for result in results), 2
            ),
        ))
    return rows
