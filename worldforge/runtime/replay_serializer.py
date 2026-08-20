"""Serialization helpers for reproducible runtime replay artifacts.

The serializer keeps evidence portable between analysis, QA, and CI systems.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .replay_artifact import ReplayArtifact


def replay_artifact_payload(artifact: ReplayArtifact) -> dict[str, Any]:
    return {
        "build": artifact.build,
        "input_trace": artifact.input_trace,
        "runtime_events": artifact.runtime_events,
        "verification": artifact.verification,
    }


def serialize_replay_artifact(artifact: ReplayArtifact) -> str:
    payload = replay_artifact_payload(artifact)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def replay_artifact_digest(artifact: ReplayArtifact) -> str:
    return hashlib.sha256(serialize_replay_artifact(artifact).encode("utf-8")).hexdigest()
