from dataclasses import dataclass

from worldforge.runtime.replay_execution import ReplayExecutionEngine


@dataclass
class Trace:
    seed: int = 42
    actions: list = None

    def __post_init__(self):
        if self.actions is None:
            self.actions = ["move"]


class Provider:
    def __init__(self):
        self.calls = []

    def load_build(self, build):
        self.calls.append(("load", build))

    def reset(self, seed):
        self.calls.append(("reset", seed))

    def apply_input(self, action):
        self.calls.append(("input", action))

    def collect_events(self):
        return [{"type": "damage"}]

    def shutdown(self):
        self.calls.append(("shutdown",))


class Verifier:
    def verify(self, events):
        return {"passed": True}


def test_replay_execution_runs_full_lifecycle():
    provider = Provider()
    result = ReplayExecutionEngine(provider, Verifier()).execute(
        "build-1", Trace(), "replay-1"
    )

    assert result.success is True
    assert result.replay_id == "replay-1"
    assert provider.calls[0] == ("load", "build-1")
    assert provider.calls[-1] == ("shutdown",)
