"""Portable replay bundle representation."""

from __future__ import annotations

from dataclasses import dataclass

from .replay_artifact import ReplayArtifact
from .replay_serializer import replay_artifact_digest


@dataclass(frozen=True)
class ReplayBundle:
    artifact: ReplayArtifact
    replay_id: str

    @classmethod
    def from_artifact(cls, artifact: ReplayArtifact) -> "ReplayBundle":
        return cls(
            artifact=artifact,
            replay_id=replay_artifact_digest(artifact),
        )
