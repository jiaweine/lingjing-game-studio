from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class LearnerState:
    """Long-term learner cognitive state."""

    learner_id: str
    mastery: Dict[str, float] = field(default_factory=dict)
    misconceptions: List[str] = field(default_factory=list)
    forgetting: Dict[str, float] = field(default_factory=dict)
    motivation: float = 0.5
    cognitive_load: float = 0.5
    ai_dependency: float = 0.0

    def update_mastery(self, concept: str, value: float):
        self.mastery[concept] = max(0.0, min(1.0, value))
