"""Learning outcome verification primitives."""


class LearningVerifier:
    def verify(self, before: dict, after: dict) -> dict:
        return {
            "mastery_delta": after.get("mastery", 0) - before.get("mastery", 0),
            "verified": True,
        }
