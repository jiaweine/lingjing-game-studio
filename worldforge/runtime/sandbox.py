from __future__ import annotations

from dataclasses import dataclass

from worldforge.models import ActionKind, WorldState


@dataclass(frozen=True)
class SandboxDecision:
    allowed: bool
    reason: str
    risk: float


class ActionSandbox:
    """Session-scoped pre-execution guard for game-side effects and action budgets.

    A sandbox instance must never be shared between concurrent runs: its history is part of the
    execution state. The engine creates one instance per run and only records actions that survive
    verification. Rolled-back actions are explicitly removed from the budget history.
    """

    def __init__(self, max_irreversible_per_window: int = 4) -> None:
        self.max_irreversible_per_window = max(1, int(max_irreversible_per_window))
        self.history: list[str] = []

    def validate(self, action: ActionKind, state: WorldState, legal: list[str]) -> SandboxDecision:
        if action.value not in legal:
            return SandboxDecision(False, "action_not_in_environment_contract", 1.0)
        hp_ratio = state.player_hp / max(1, state.player_max_hp)
        irreversible = action in {
            ActionKind.HEAVY_ATTACK,
            ActionKind.CAST,
            ActionKind.BUY_BLADE,
            ActionKind.BUY_ARMOR,
            ActionKind.RETREAT,
        }
        irreversible_values = {
            ActionKind.HEAVY_ATTACK.value,
            ActionKind.CAST.value,
            ActionKind.BUY_BLADE.value,
            ActionKind.BUY_ARMOR.value,
            ActionKind.RETREAT.value,
        }
        recent = self.history[-5:]
        if irreversible and sum(x in irreversible_values for x in recent) >= self.max_irreversible_per_window:
            return SandboxDecision(False, "irreversible_action_budget_exceeded", 0.82)
        risk = state.threat * 0.6 + (1 - hp_ratio) * 0.4
        if action == ActionKind.RETREAT and hp_ratio > 0.45:
            return SandboxDecision(False, "premature_termination", risk)
        return SandboxDecision(True, "contract_valid", min(1.0, risk))

    def record(self, action: ActionKind) -> None:
        self.history.append(action.value)

    def undo(self, action: ActionKind) -> None:
        """Remove the most recent matching action after a verifier rollback."""
        if self.history and self.history[-1] == action.value:
            self.history.pop()
            return
        for idx in range(len(self.history) - 1, -1, -1):
            if self.history[idx] == action.value:
                del self.history[idx]
                break

    def snapshot(self) -> tuple[str, ...]:
        return tuple(self.history)
