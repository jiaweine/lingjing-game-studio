"""Deterministic replay primitives for real game runtimes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class InputAction:
    frame: int
    action: str
    value: dict[str, Any]


@dataclass(frozen=True)
class InputTrace:
    actions: tuple[InputAction, ...]
    seed: int | None = None

    def append(self, action: InputAction) -> "InputTrace":
        return InputTrace(actions=self.actions + (action,), seed=self.seed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "actions": [
                {"frame": item.frame, "action": item.action, "value": item.value}
                for item in self.actions
            ],
        }
