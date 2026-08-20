"""Unreal Engine runtime provider boundary.

This module intentionally defines the integration boundary without pretending
that an Unreal automation bridge exists yet. Production adapters can implement
these hooks using Unreal Automation Tool, editor scripting, or a remote runner.
"""

from dataclasses import dataclass
from typing import Any

from .base import RuntimeProvider


@dataclass
class UnrealRuntimeProvider(RuntimeProvider):
    """Placeholder provider contract for Unreal-based games."""

    build_path: str | None = None

    def load_build(self, build_ref: Any) -> None:
        self.build_path = getattr(build_ref, "build_path", None)

    def reset(self, seed: int | None = None) -> dict[str, Any]:
        return {"seed": seed, "status": "unreal_reset_not_connected"}

    def apply_input(self, action: dict[str, Any]) -> None:
        raise NotImplementedError("Unreal input bridge is not connected")

    def capture_state(self) -> dict[str, Any]:
        return {"provider": "unreal", "connected": False}

    def capture_frame(self) -> bytes | None:
        return None

    def collect_events(self) -> list[dict[str, Any]]:
        return []

    def shutdown(self) -> None:
        return None
