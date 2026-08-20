from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class RuntimeProvider(ABC):
    """Adapter boundary between Lingjing and an executable game runtime.

    Implementations can target synthetic simulations, Unity builds, Unreal
    builds, or remote execution environments. The core verifier only consumes
    provider observations and events.
    """

    @abstractmethod
    def load_build(self, build_ref: Any) -> None:
        pass

    @abstractmethod
    def reset(self, seed: int | None = None) -> Any:
        pass

    @abstractmethod
    def apply_input(self, input_action: Any) -> Any:
        pass

    @abstractmethod
    def capture_state(self) -> Any:
        pass

    @abstractmethod
    def capture_frame(self) -> bytes | None:
        pass

    @abstractmethod
    def collect_events(self) -> list[Any]:
        pass

    @abstractmethod
    def shutdown(self) -> None:
        pass
