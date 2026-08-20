"""Replay runtime command helpers.

This module keeps replay execution accessible from automation and future CLI wiring.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ReplayCommandResult:
    replay_id: str
    success: bool
    message: str


def describe_replay(replay_id: str) -> ReplayCommandResult:
    return ReplayCommandResult(
        replay_id=replay_id,
        success=True,
        message=f"Replay {replay_id} is ready for execution",
    )
