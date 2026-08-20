"""Replay execution orchestration.

Coordinates a ReplayBundle with a RuntimeProvider without coupling the
verification layer to a specific game engine.
"""

from dataclasses import dataclass
from typing import Any

from .input_trace import InputTrace


@dataclass(frozen=True)
class ReplayExecutionResult:
    replay_id: str
    events: list[dict[str, Any]]
    verification: dict[str, Any]
    success: bool


class ReplayExecutionEngine:
    def __init__(self, provider, verifier=None):
        self.provider = provider
        self.verifier = verifier

    def execute(self, build_ref: str, trace: InputTrace, replay_id: str):
        self.provider.load_build(build_ref)
        try:
            self.provider.reset(seed=trace.seed)

            for action in trace.actions:
                self.provider.apply_input(action)

            events = self.provider.collect_events()
            verification = {}
            success = True

            if self.verifier:
                verification = self.verifier.verify(events)
                success = bool(verification.get("passed", False))

            return ReplayExecutionResult(
                replay_id=replay_id,
                events=events,
                verification=verification,
                success=success,
            )
        finally:
            self.provider.shutdown()
