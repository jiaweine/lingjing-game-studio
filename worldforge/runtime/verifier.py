from __future__ import annotations

from dataclasses import dataclass

from worldforge.models import GoalState, WorldState

from .harness_genome import HarnessGenomeStore


@dataclass
class Verification:
    ok: bool
    severity: str
    violations: list[str]
    risk_score: float
    recommendation: str


class StateVerifier:
    """Frozen-kernel invariant verifier.

    Harness evolution may change branch utility but cannot remove execution invariants,
    anomaly reporting, rollback semantics or the authority of this verifier.
    """

    def verify(
        self,
        before: WorldState,
        after: WorldState,
        info: dict,
        goal: GoalState,
        anomalies: list[str],
    ) -> Verification:
        violations: list[str] = []
        severity = "info"

        if after.gold < 0:
            violations.append("negative_gold")
        if after.energy < 0 or after.energy > after.max_energy:
            violations.append("energy_invariant")
        if after.player_hp > after.player_max_hp:
            violations.append("hp_invariant")
        if info.get("invalid"):
            violations.append("invalid_action")
        findings = [f"finding:{name}" for name in sorted(set(anomalies))]
        violations.extend(findings)

        hp_ratio = after.player_hp / max(1, after.player_max_hp)
        # Frozen safety contract: the harness cannot mutate the judge to make unsafe states safe.
        risk = min(
            1.0,
            max(0.0, after.threat * .65 + (1.0 - hp_ratio) * .55),
        )
        if (
            not after.terminal
            and hp_ratio < goal.min_health_ratio
            and after.enemy_hp > after.enemy_max_hp * .25
        ):
            violations.append("catastrophic_survival_risk")
        if after.terminal and after.outcome == "defeat":
            violations.append("terminal_failure")

        unsafe = [item for item in violations if not item.startswith("finding:")]
        if any(
            violation in unsafe
            for violation in ["negative_gold", "energy_invariant", "hp_invariant"]
        ):
            severity = "critical"
        elif unsafe or findings:
            severity = "warning"

        recommendation = "continue"
        if "terminal_failure" in unsafe:
            recommendation = "rollback"
        elif "catastrophic_survival_risk" in unsafe:
            recommendation = "replan"
        elif findings:
            recommendation = "flag_and_continue"
        return Verification(
            ok=not unsafe,
            severity=severity,
            violations=violations,
            risk_score=round(risk, 4),
            recommendation=recommendation,
        )

    def branch_score(
        self,
        state: WorldState,
        reward: float,
        goal: GoalState,
        violations: list[str],
    ) -> float:
        gene = HarnessGenomeStore.current().utility
        hp_ratio = state.player_hp / max(1, state.player_max_hp)
        enemy_ratio = state.enemy_hp / max(1, state.enemy_max_hp)
        utility = (
            reward
            + (1.0 - enemy_ratio) * gene.progress_weight
            + hp_ratio * gene.health_weight
            + state.gold * gene.gold_weight
        )
        utility -= (
            max(0.0, state.threat - goal.risk_tolerance)
            * gene.threat_penalty
        )
        unsafe_count = sum(
            not violation.startswith("finding:") for violation in violations
        )
        utility -= unsafe_count * gene.violation_penalty
        if state.outcome == "victory":
            utility += gene.victory_bonus
        if state.outcome == "defeat":
            utility -= gene.defeat_penalty
        return utility
