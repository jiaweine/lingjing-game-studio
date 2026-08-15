from __future__ import annotations

from dataclasses import dataclass
from worldforge.models import GoalState, WorldState


@dataclass
class Verification:
    ok: bool
    severity: str
    violations: list[str]
    risk_score: float
    recommendation: str


class StateVerifier:
    """Verifies invariants, safety, side effects and suspicious reward loops."""

    def verify(self, before: WorldState, after: WorldState, info: dict, goal: GoalState, anomalies: list[str]) -> Verification:
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
        if "reward_loop" in anomalies:
            violations.append("reward_loop_anomaly")
        hp_ratio = after.player_hp / max(1, after.player_max_hp)
        risk = min(1.0, max(0.0, after.threat * .65 + (1.0 - hp_ratio) * .55))
        if not after.terminal and hp_ratio < goal.min_health_ratio and after.enemy_hp > after.enemy_max_hp * .25:
            violations.append("catastrophic_survival_risk")
        if after.terminal and after.outcome == "defeat":
            violations.append("terminal_failure")

        if any(v in violations for v in ["negative_gold", "energy_invariant", "hp_invariant"]):
            severity = "critical"
        elif any(v in violations for v in ["catastrophic_survival_risk", "terminal_failure", "reward_loop_anomaly"]):
            severity = "warning"

        rec = "continue"
        if "terminal_failure" in violations:
            rec = "rollback"
        elif "catastrophic_survival_risk" in violations:
            rec = "replan"
        elif "reward_loop_anomaly" in violations:
            rec = "flag_and_continue"
        return Verification(ok=not violations, severity=severity, violations=violations, risk_score=round(risk, 4), recommendation=rec)

    def branch_score(self, state: WorldState, reward: float, goal: GoalState, violations: list[str]) -> float:
        hp_ratio = state.player_hp / max(1, state.player_max_hp)
        enemy_ratio = state.enemy_hp / max(1, state.enemy_max_hp)
        utility = reward + (1.0 - enemy_ratio) * 24 + hp_ratio * 17 + state.gold * .04
        utility -= max(0.0, state.threat - goal.risk_tolerance) * 26
        utility -= len(violations) * 8
        if state.outcome == "victory": utility += 70
        if state.outcome == "defeat": utility -= 90
        return utility
