from __future__ import annotations
from dataclasses import dataclass
from worldforge.models import ActionKind, WorldState


@dataclass
class SandboxDecision:
    allowed: bool
    reason: str
    risk: float


class ActionSandbox:
    """Pre-execution guard for game-side effects and action budgets."""
    def __init__(self, max_irreversible_per_window: int = 4) -> None:
        self.max_irreversible_per_window = max_irreversible_per_window
        self.history: list[str] = []

    def validate(self, action: ActionKind, state: WorldState, legal: list[str]) -> SandboxDecision:
        if action.value not in legal:
            return SandboxDecision(False, "action_not_in_environment_contract", 1.0)
        hp_ratio = state.player_hp / max(1,state.player_max_hp)
        irreversible = action in {ActionKind.HEAVY_ATTACK, ActionKind.CAST, ActionKind.BUY_BLADE, ActionKind.BUY_ARMOR, ActionKind.RETREAT}
        recent = self.history[-5:]
        if irreversible and sum(x in {ActionKind.HEAVY_ATTACK.value,ActionKind.CAST.value,ActionKind.BUY_BLADE.value,ActionKind.BUY_ARMOR.value} for x in recent) >= self.max_irreversible_per_window:
            return SandboxDecision(False, "irreversible_action_budget_exceeded", .82)
        risk = state.threat * .6 + (1-hp_ratio)*.4
        if action == ActionKind.RETREAT and hp_ratio > .45:
            return SandboxDecision(False, "premature_termination", risk)
        return SandboxDecision(True, "contract_valid", min(1.0,risk))

    def record(self, action: ActionKind) -> None:
        self.history.append(action.value)
