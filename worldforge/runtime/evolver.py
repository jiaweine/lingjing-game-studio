from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FailureSignal:
    category: str
    reason: str


class FailureDrivenEvolver:
    """Deprecated compatibility adapter.

    Category-specific skill deltas were intentionally removed. The product Runtime now evolves
    the complete HarnessGenome through evidence-linked population search in
    ``harness_evolution.py``. Keeping this no-op class avoids breaking direct imports of the
    frozen base engine while ensuring the legacy rule table cannot influence behavior.
    """

    def __init__(self, skill_bank):
        self.skill_bank = skill_bank

    @staticmethod
    def attribute(**kwargs):
        return None

    @staticmethod
    def evolve(*args, **kwargs):
        raise RuntimeError(
            "Rule-based skill evolution is retired; use HarnessEvolutionEngine instead."
        )
