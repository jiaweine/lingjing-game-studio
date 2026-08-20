"""Stable identity primitives for real game runtime builds.

A build fingerprint identifies the exact executable/configuration that produced
runtime evidence. It intentionally stores no user/session data.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
import hashlib
import json
from typing import Any


@dataclass(frozen=True)
class GameBuildFingerprint:
    engine: str
    engine_version: str
    game_version: str
    git_sha: str
    asset_hash: str = ""
    config_hash: str = ""
    binary_hash: str = ""

    def canonical_payload(self) -> dict[str, Any]:
        return asdict(self)

    def digest(self) -> str:
        payload = json.dumps(self.canonical_payload(), sort_keys=True).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()
