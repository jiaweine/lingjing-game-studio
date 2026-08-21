from abc import ABC, abstractmethod


class LearnForgeSkill(ABC):
    """Composable education agent skill."""

    name = "base"

    @abstractmethod
    def execute(self, context: dict) -> dict:
        raise NotImplementedError

    def evaluate(self, result: dict) -> dict:
        return {"status": "unevaluated"}
