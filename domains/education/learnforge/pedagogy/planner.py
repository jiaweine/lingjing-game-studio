from dataclasses import dataclass, field


@dataclass
class TeachingPlan:
    strategy: str
    steps: list[str] = field(default_factory=list)


class PedagogyPlanner:
    """Selects teaching approaches from learner context."""

    def plan(self, diagnosis: dict) -> TeachingPlan:
        if diagnosis.get("root_cause") == "misconception":
            return TeachingPlan(
                strategy="socratic_guided_discovery",
                steps=["diagnose", "question", "explain", "practice"],
            )

        return TeachingPlan(
            strategy="adaptive_explanation",
            steps=["explain", "practice", "verify"],
        )
