from __future__ import annotations

from typing import Any

from .base import RuntimeProvider


class SyntheticWorldForgeProvider(RuntimeProvider):
    """Provider placeholder for WorldForge synthetic execution.

    Keeping synthetic execution behind the same boundary as real runtimes
    prevents evidence semantics from depending on a specific engine.
    """

    def __init__(self, engine: Any) -> None:
        self.engine = engine

    def load_build(self, build_ref: Any) -> None:
        self.engine.build_ref = build_ref

    def reset(self, seed: int | None = None) -> Any:
        return self.engine.reset(seed=seed) if hasattr(self.engine, "reset") else None

    def apply_input(self, input_action: Any) -> Any:
        return self.engine.step(input_action) if hasattr(self.engine, "step") else None

    def capture_state(self) -> Any:
        return getattr(self.engine, "state", None)

    def capture_frame(self) -> bytes | None:
        return None

    def collect_events(self) -> list[Any]:
        return list(getattr(self.engine, "events", []))

    def shutdown(self) -> None:
        return None
