from worldforge.runtime.providers.base import RuntimeProvider


class DummyProvider(RuntimeProvider):
    def load_build(self, build):
        return {"build": build}

    def reset(self, seed=None):
        return {"seed": seed}

    def apply_input(self, action):
        return action

    def capture_state(self):
        return {"ok": True}

    def capture_frame(self):
        return b"frame"

    def collect_events(self):
        return []

    def shutdown(self):
        return None


def test_provider_contract_minimum_runtime_flow():
    provider = DummyProvider()

    assert provider.load_build("test-build")["build"] == "test-build"
    assert provider.reset(42)["seed"] == 42
    assert provider.apply_input({"action": "move"})["action"] == "move"
    assert provider.capture_state()["ok"] is True
    assert provider.capture_frame() == b"frame"
    assert provider.collect_events() == []
