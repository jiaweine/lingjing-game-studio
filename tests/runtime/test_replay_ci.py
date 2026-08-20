from worldforge.runtime.replay_ci import ReplayCIValidator


class _Runner:
    def execute(self, bundle):
        return type("Result", (), {"success": True})()


def test_replay_ci_validator_accepts_successful_execution():
    validator = ReplayCIValidator(_Runner())
    result = validator.validate(object())

    assert result.success is True
