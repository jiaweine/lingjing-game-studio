class DiagnosisResult:
    def __init__(self, root_cause: str, confidence: float):
        self.root_cause = root_cause
        self.confidence = confidence


class DiagnosisEngine:
    """Analyze why a learner failed instead of only detecting wrong answers."""

    def diagnose(self, learner_state, problem, answer):
        return DiagnosisResult(
            root_cause="unknown",
            confidence=0.0,
        )
