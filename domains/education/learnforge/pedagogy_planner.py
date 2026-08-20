"""Adaptive teaching strategy planner."""

from dataclasses import dataclass


@dataclass
class TeachingPlan:
    strategy: str
    steps: list[str]


class PedagogyPlanner:
    def plan(self, diagnosis: dict) -> TeachingPlan:
        if diagnosis.get("root_cause") == "misconception":
            return TeachingPlan(
                strategy="socratic",
                steps=["question", "guide", "practice", "verify"],
            )
        return TeachingPlan(
            strategy="explanation",
            steps=["explain", "practice", "verify"],
        )
