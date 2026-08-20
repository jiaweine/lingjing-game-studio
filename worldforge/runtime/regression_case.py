"""Regression cases generated from verified runtime evidence."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RegressionCase:
    title: str
    build_fingerprint: str
    steps: tuple[str, ...] = field(default_factory=tuple)
    assertions: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "build_fingerprint": self.build_fingerprint,
            "steps": list(self.steps),
            "assertions": list(self.assertions),
        }
