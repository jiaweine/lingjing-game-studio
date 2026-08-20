from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReplayCIResult:
    replay_id: str
    passed: bool
    verification_summary: str


class ReplayCIValidator:
    """Small CI-facing boundary for replay regression checks."""

    def validate(self, replay_result) -> ReplayCIResult:
        verification = getattr(replay_result, "verification", None)
        passed = bool(getattr(verification, "passed", False))
        summary = "verified" if passed else "verification_failed"
        return ReplayCIResult(
            replay_id=str(getattr(replay_result, "replay_id", "unknown")),
            passed=passed,
            verification_summary=summary,
        )
