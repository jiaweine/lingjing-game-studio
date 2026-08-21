class LearnForgeRuntime:
    """Education domain runtime.

    Coordinates learner modeling, diagnosis, pedagogy and evaluation.
    """

    def __init__(self, learner_engine=None, diagnosis_engine=None):
        self.learner_engine = learner_engine
        self.diagnosis_engine = diagnosis_engine

    def run_session(self, learner, problem, answer):
        diagnosis = self.diagnosis_engine.diagnose(
            learner,
            problem,
            answer,
        ) if self.diagnosis_engine else None

        return {
            "diagnosis": diagnosis,
        }
