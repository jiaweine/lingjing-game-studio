"""Evolvable teaching strategy representation."""

from dataclasses import dataclass, field


@dataclass
class TeachingGenome:
    strategy: str
    parameters: dict = field(default_factory=dict)
    topology: list[str] = field(default_factory=list)
    generation: int = 0

    def mutate(self, change: dict):
        self.parameters.update(change)
        self.generation += 1
        return self
