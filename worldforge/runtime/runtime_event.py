"""Engine-agnostic runtime event primitives.

Runtime providers emit these events without exposing engine-specific details.
Verifier and analysis layers consume normalized events.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class RuntimeEvent:
    event_type: str
    source: str
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DamageEvent(RuntimeEvent):
    event_type: str = "damage"


@dataclass(frozen=True)
class StateChangeEvent(RuntimeEvent):
    event_type: str = "state_change"


@dataclass(frozen=True)
class PerformanceEvent(RuntimeEvent):
    event_type: str = "performance"
