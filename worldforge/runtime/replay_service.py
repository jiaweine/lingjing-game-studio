from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .replay_execution import ReplayExecutionEngine, ReplayExecutionResult
from .replay_bundle import ReplayBundle


@dataclass
class ReplayServiceResult:
    replay_id: str
    result: ReplayExecutionResult


class ReplayService:
    """Coordinates replay execution and persistence boundaries.

    Storage is injected so replay execution remains testable and provider agnostic.
    """

    def __init__(self, executor: ReplayExecutionEngine, store: Any):
        self.executor = executor
        self.store = store

    def execute(self, bundle: ReplayBundle) -> ReplayServiceResult:
        result = self.executor.execute(bundle)
        replay_id = self.store.save(result)
        return ReplayServiceResult(replay_id=replay_id, result=result)
