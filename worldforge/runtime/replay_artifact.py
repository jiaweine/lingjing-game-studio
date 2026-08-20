"""Serializable replay artifact boundary.

A replay artifact combines the identity of a build, the input sequence,
and verification outputs into one portable engineering record.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ReplayArtifact:
    build: dict[str, Any]
    input_trace: dict[str, Any]
    runtime_events: list[dict[str, Any]] = field(default_factory=list)
    verification: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "build": self.build,
            "input_trace": self.input_trace,
            "runtime_events": list(self.runtime_events),
            "verification": self.verification,
        }
