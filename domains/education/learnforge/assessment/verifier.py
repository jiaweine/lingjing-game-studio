class LearningVerifier:
    """Verifies learning outcome beyond immediate correctness."""

    def verify(self, before: dict, after: dict) -> dict:
        return {
            "mastery_gain": after.get("mastery", 0) - before.get("mastery", 0),
            "retention_check": False,
            "transfer_check": False,
        }
