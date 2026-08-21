from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EvidenceProvenance(str, Enum):
    OBSERVED = "observed"
    REPRODUCED = "reproduced"
    SYNTHETIC = "synthetic"
    INFERRED = "inferred"


@dataclass(frozen=True)
class GameBuildRef:
    build_id: str
    engine: str
    version: str | None = None
    source_revision: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionAction:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutionObservation:
    state: dict[str, Any] = field(default_factory=dict)
    logs: list[str] = field(default_factory=list)
    metrics: dict[str, float | int | str | bool] = field(default_factory=dict)
    frame_path: str | None = None
    video_path: str | None = None
    timestamp: float | None = None
    provenance: EvidenceProvenance = EvidenceProvenance.REPRODUCED

    def __post_init__(self) -> None:
        if self.provenance is not EvidenceProvenance.REPRODUCED:
            raise ValueError(
                "GameExecutionAdapter observations must be marked as reproduced evidence"
            )


@dataclass(frozen=True)
class ExecutionCheckpoint:
    checkpoint_id: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VerificationResult:
    passed: bool
    assertion: str
    details: dict[str, Any] = field(default_factory=dict)


class GameExecutionAdapter(ABC):
    """Boundary between the frozen kernel and a real game runtime.

    Implementations own engine-specific concerns such as launching builds,
    input injection, save-state handling, frame capture, log collection and
    telemetry. The WorldForge kernel should consume this contract rather than
    importing Unity, Unreal or platform-specific SDKs directly.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def engine(self) -> str:
        raise NotImplementedError

    @abstractmethod
    async def load_build(self, build: GameBuildRef) -> None:
        """Load or launch the requested game build."""
        raise NotImplementedError

    @abstractmethod
    async def reset(self, *, seed: int | None = None) -> ExecutionObservation:
        """Reset to a deterministic starting point when the game supports it."""
        raise NotImplementedError

    @abstractmethod
    async def perform_action(self, action: ExecutionAction) -> ExecutionObservation:
        """Apply one externally observable player/test action and return evidence."""
        raise NotImplementedError

    @abstractmethod
    async def observe(self) -> ExecutionObservation:
        """Capture the current state, logs, metrics and visual evidence."""
        raise NotImplementedError

    @abstractmethod
    async def checkpoint(self) -> ExecutionCheckpoint:
        """Create a restorable checkpoint or save-state reference."""
        raise NotImplementedError

    @abstractmethod
    async def restore(self, checkpoint: ExecutionCheckpoint) -> ExecutionObservation:
        """Restore a previously created checkpoint."""
        raise NotImplementedError

    @abstractmethod
    async def verify_condition(
        self,
        assertion: str,
        *,
        expected: Any = None,
    ) -> VerificationResult:
        """Evaluate a concrete condition against the real running game."""
        raise NotImplementedError

    async def close(self) -> None:
        """Release build processes or runner resources."""


class GameExecutionAdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, GameExecutionAdapter] = {}

    def register(self, adapter: GameExecutionAdapter) -> None:
        key = adapter.name.strip()
        if not key:
            raise ValueError("adapter name must not be empty")
        if key in self._adapters:
            raise ValueError(f"adapter already registered: {key}")
        self._adapters[key] = adapter

    def get(self, name: str) -> GameExecutionAdapter:
        try:
            return self._adapters[name]
        except KeyError as exc:
            raise KeyError(f"unknown game execution adapter: {name}") from exc

    def describe(self) -> list[dict[str, str]]:
        return [
            {"name": adapter.name, "engine": adapter.engine}
            for adapter in self._adapters.values()
        ]
