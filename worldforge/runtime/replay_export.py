from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .replay_bundle import ReplayBundle


def export_replay_bundle(bundle: ReplayBundle, output_dir: str | Path) -> Path:
    """Export a replay bundle as portable engineering artifacts.

    The exporter intentionally keeps artifacts deterministic so they can be
    consumed by CI replay jobs or shared with QA teams.
    """
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)

    payloads: dict[str, Any] = {
        "manifest.json": getattr(bundle, "manifest", {}),
        "artifact.json": getattr(bundle, "artifact", {}),
        "verification.json": getattr(bundle, "verification", {}),
    }

    for name, payload in payloads.items():
        path = root / name
        path.write_text(
            json.dumps(payload, indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )

    return root
