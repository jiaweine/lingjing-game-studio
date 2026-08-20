"""Replay manifest metadata for portable reproduction bundles."""

from dataclasses import dataclass, asdict
from typing import Any, Dict


@dataclass(frozen=True)
class ReplayManifest:
    lingjing_version: str
    kernel_version: str
    runtime_provider: str
    build_digest: str
    verifier_version: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
