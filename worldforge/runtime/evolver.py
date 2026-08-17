from __future__ import annotations

import uuid
from dataclasses import dataclass

from worldforge.models import EvolutionPatch


@dataclass
class FailureSignal:
    category: str
    skill_id: str
    action: str
    delta: float
    reason: str


class FailureDrivenEvolver:
    def __init__(self, skill_bank):
        self.skill_bank = skill_bank

    def attribute(self, *, outcome, min_hp, farm_count, invalid_actions, last_action):
        if invalid_actions > 0:
            return FailureSignal("execution", "survival_guard", "defend", .35, "invalid action occurred under risk")
        if outcome == "defeat" and min_hp < 30:
            return FailureSignal("survival", "survival_guard", "defend", .55, "terminal failure followed a high-risk state")
        if farm_count >= 4:
            return FailureSignal("economy", "economy_guard", "farm", -.45, "repetitive farming created low-value loop")
        if outcome != "victory" and last_action:
            return FailureSignal("progress", "burst_window", "heavy_attack", .25, "run timed out without enough progress")
        return None

    def evolve(self, signal, regression_eval, *, human_approved: bool = True):
        before = self.skill_bank.skills[signal.skill_id].model_copy(deep=True)
        candidate = self.skill_bank.propose_patch(signal.skill_id, signal.action, signal.delta, signal.reason)
        baseline = regression_eval(None)
        score = regression_eval(candidate)
        accepted = human_approved and score >= baseline - .01 and score >= baseline + .005
        if accepted:
            self.skill_bank.accept(candidate)
        reason = signal.reason if human_approved else f"{signal.reason}; blocked by human feedback gate"
        return EvolutionPatch(
            patch_id=f"patch-{uuid.uuid4().hex[:8]}",
            reason=reason,
            target_skill_id=signal.skill_id,
            before=before,
            after=candidate,
            regression_before=round(baseline, 4),
            regression_after=round(score, 4),
            accepted=accepted,
        )
