from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any


class ReplayManifestBuilder:
    """Build a stable manifest for replay portability and auditing."""

    def build(
        self,
        *,
        replay_id: str,
        build_digest: str,
        runtime_provider: str,
        verifier_version: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "replay_id": replay_id,
            "build_digest": build_digest,
            "runtime_provider": runtime_provider,
            "verifier_version": verifier_version,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "metadata": metadata or {},
        }
