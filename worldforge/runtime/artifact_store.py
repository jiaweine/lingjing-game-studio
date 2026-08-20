"""Persistent storage abstraction for replay artifacts.

The implementation keeps storage backend agnostic so local files,
object storage, or CI artifact stores can be plugged in later.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from .replay_artifact import ReplayArtifact
from .replay_serializer import replay_artifact_digest


@dataclass
class StoredReplayArtifact:
    replay_id: str
    digest: str
    artifact: ReplayArtifact


class ReplayArtifactStore:
    """Minimal deterministic replay artifact store."""

    def __init__(self) -> None:
        self._items: Dict[str, StoredReplayArtifact] = {}

    def save(self, artifact: ReplayArtifact) -> StoredReplayArtifact:
        digest = replay_artifact_digest(artifact)
        record = StoredReplayArtifact(
            replay_id=digest[:16],
            digest=digest,
            artifact=artifact,
        )
        self._items[record.replay_id] = record
        return record

    def load(self, replay_id: str) -> StoredReplayArtifact | None:
        return self._items.get(replay_id)

    def verify_digest(self, replay_id: str) -> bool:
        record = self.load(replay_id)
        if record is None:
            return False
        return replay_artifact_digest(record.artifact) == record.digest
