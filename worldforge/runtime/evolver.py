from __future__ import annotations

from dataclasses import dataclass
import uuid

from worldforge.models import EvolutionPatch


@dataclass
class FailureSignal:
    category: str
    skill_id: str
    action: str
    delta: float
    reason: str


class FailureDrivenEvolver:
    """Deprecated compatibility adapter with no domain policy table.

    The product entrypoint does not use this class: ``SelfEvolvingWorldForgeEngine`` replaces
    it with HarnessEvolutionEngine. This adapter only preserves older API callers while making
    sure no category-specific ``failure -> skill/action/delta`` rule can affect the Runtime.
    """

    def __init__(self, skill_bank):
        self.skill_bank = skill_bank

    def attribute(
        self,
        *,
        outcome=None,
        invalid_actions=0,
        last_action=None,
        **observations,
    ):
        if outcome == "victory" and not invalid_actions:
            return None
        active = [
            skill
            for skill in self.skill_bank.skills.values()
            if skill.status == "active"
        ]
        if not active:
            return None
        # Compatibility target selection is data-driven and deliberately has zero mutation.
        target = min(active, key=lambda skill: (skill.success_rate, skill.skill_id))
        action = last_action or next(iter(target.action_bias), "")
        return FailureSignal(
            category="legacy-compatibility",
            skill_id=target.skill_id,
            action=action,
            delta=0.0,
            reason=(
                f"legacy compatibility evidence; outcome={outcome}; "
                f"invalid_actions={invalid_actions}"
            ),
        )

    def evolve(self, signal, regression_eval, *, human_approved: bool = True):
        before = self.skill_bank.skills[signal.skill_id].model_copy(deep=True)
        candidate = before.model_copy(deep=True)
        candidate.status = "candidate"
        baseline = regression_eval(None)
        score = regression_eval(candidate)
        reason = signal.reason
        if not human_approved:
            reason += "; blocked by human feedback gate"
        else:
            reason += "; legacy skill mutation retired in favor of HarnessEvolutionEngine"
        return EvolutionPatch(
            patch_id=f"legacy-{uuid.uuid4().hex[:8]}",
            reason=reason,
            target_skill_id=signal.skill_id,
            before=before,
            after=candidate,
            regression_before=round(baseline, 4),
            regression_after=round(score, 4),
            accepted=False,
        )
