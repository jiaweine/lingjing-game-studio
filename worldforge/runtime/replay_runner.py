"""Replay execution orchestration primitives."""

from dataclasses import dataclass
from typing import Any, Dict


@dataclass(frozen=True)
class ReplayExecutionResult:
    replay_id: str
    passed: bool
    details: Dict[str, Any]


class ReplayRunner:
    """Engine-agnostic replay coordinator.

    Concrete providers execute the actual game runtime. This layer only
    coordinates replay lifecycle and verification output.
    """

    def __init__(self, provider: Any):
        self.provider = provider

    def run(self, artifact: Any) -> ReplayExecutionResult:
        self.provider.load_build(artifact.build)
        self.provider.reset()
        self.provider.shutdown()

        return ReplayExecutionResult(
            replay_id=getattr(artifact, "replay_id", "unknown"),
            passed=False,
            details={"status": "execution_pipeline_ready"},
        )
