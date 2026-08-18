from __future__ import annotations

from collections.abc import Iterable

from .harness_evolution import EvolutionEvidence
from .harness_genome import state_features


class TraceReflector:
    """Turn runtime evidence into edit pressure without game-specific policy rules.

    The reflector does not know action semantics, specialist roles, scenario names, or tag
    names. It scores every numeric feature exposed by the active representation and lets the
    search/evaluation loop decide whether edits aligned with those features are useful.
    """

    @classmethod
    def diagnose(
        cls,
        *,
        state,
        belief,
        goal,
        outcome: str | None,
        anomalies: Iterable[str],
        invalid_actions: int,
        action_counts: dict[str, int],
    ) -> EvolutionEvidence:
        features = state_features(state, belief, goal)
        priorities = {
            name: abs(float(value))
            for name, value in features.items()
            if name != "bias" and isinstance(value, (int, float))
        }
        if not priorities:
            priorities = {"uncertainty": abs(float(belief.uncertainty))}

        # Repetitive behavior increases edit pressure globally without assuming which action is
        # good or bad. Candidate credit still comes only from the frozen train/held-out judge.
        if action_counts:
            total = max(1, sum(action_counts.values()))
            concentration = max(action_counts.values()) / total
            multiplier = 1.0 + concentration
            priorities = {
                name: value * multiplier for name, value in priorities.items()
            }

        anomaly_list = sorted(set(anomalies))
        if anomaly_list:
            why = "anomaly:" + "+".join(anomaly_list)
        elif invalid_actions:
            why = "invalid-action"
        else:
            why = outcome or "low-yield"

        where = max(priorities, key=priorities.get)
        ranked = sorted(priorities.items(), key=lambda item: item[1], reverse=True)
        summary = ", ".join(f"{name}={value:.3f}" for name, value in ranked[:4])
        prediction = (
            f"An evidence-aligned edit around {where} should improve sealed held-out "
            f"objective without verifier safety regression for pathology {why}."
        )
        return EvolutionEvidence(where, why, priorities, summary, prediction)
