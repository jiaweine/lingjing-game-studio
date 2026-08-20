"""Convert normalized runtime events into verification findings.

This module is intentionally conservative: runtime observations become
production findings only after passing through an explicit verification step.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from .runtime_event import RuntimeEvent


@dataclass(frozen=True)
class RuntimeFinding:
    title: str
    category: str
    source: str
    evidence: dict[str, Any] = field(default_factory=dict)
    verified: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "category": self.category,
            "source": self.source,
            "evidence": self.evidence,
            "verified": self.verified,
        }


def event_to_finding(event: RuntimeEvent) -> RuntimeFinding | None:
    """Create a candidate finding from a normalized runtime event."""
    mapping = {
        "damage": "damage_anomaly",
        "state_change": "state_inconsistency",
        "performance": "performance_regression",
    }

    category = mapping.get(event.event_type)
    if category is None:
        return None

    return RuntimeFinding(
        title=f"Detected {category}",
        category=category,
        source=event.source,
        evidence={
            "timestamp": event.timestamp,
            "payload": event.payload,
        },
    )


def events_to_findings(events: Iterable[RuntimeEvent]) -> list[RuntimeFinding]:
    """Build candidate findings while keeping verification explicit."""
    return [finding for event in events if (finding := event_to_finding(event))]
