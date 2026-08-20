"""Unity runtime provider foundation.

This module intentionally defines the integration boundary only. It does not
claim to launch or control a Unity build until a project-specific adapter is
implemented.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .base import RuntimeProvider


@dataclass
class UnityRuntimeProvider(RuntimeProvider):
    executable: Path
    build_id: str
    _running: bool = False

    def load_build(self) -> None:
        if not self.executable.exists():
            raise FileNotFoundError(self.executable)

    def reset(self, seed: int | None = None) -> dict[str, Any]:
        self._running = True
        return {"seed": seed, "provider": "unity"}

    def apply_input(self, action: dict[str, Any]) -> None:
        if not self._running:
            raise RuntimeError("Unity runtime is not running")

    def capture_state(self) -> dict[str, Any]:
        return {"build_id": self.build_id, "provider": "unity"}

    def capture_frame(self) -> bytes | None:
        return None

    def collect_events(self) -> list[Any]:
        return []

    def shutdown(self) -> None:
        self._running = False
