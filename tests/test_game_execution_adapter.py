import asyncio

import pytest

from worldforge.integrations import (
    EvidenceProvenance,
    ExecutionAction,
    ExecutionCheckpoint,
    ExecutionObservation,
    GameBuildRef,
    GameExecutionAdapter,
    GameExecutionAdapterRegistry,
    VerificationResult,
)


class FakeAdapter(GameExecutionAdapter):
    def __init__(self):
        self.loaded = None
        self.actions = []

    @property
    def name(self):
        return "fake-runner"

    @property
    def engine(self):
        return "fake"

    async def load_build(self, build):
        self.loaded = build

    async def reset(self, *, seed=None):
        return ExecutionObservation(state={"seed": seed})

    async def perform_action(self, action):
        self.actions.append(action)
        return ExecutionObservation(state={"last_action": action.name})

    async def observe(self):
        return ExecutionObservation(state={"running": True})

    async def checkpoint(self):
        return ExecutionCheckpoint("cp-1")

    async def restore(self, checkpoint):
        return ExecutionObservation(state={"restored": checkpoint.checkpoint_id})

    async def verify_condition(self, assertion, *, expected=None):
        return VerificationResult(True, assertion, {"expected": expected})


def test_execution_adapter_contract_marks_real_runtime_as_reproduced():
    async def exercise():
        adapter = FakeAdapter()
        await adapter.load_build(
            GameBuildRef(
                build_id="build-42",
                engine="fake",
                version="1.2.3",
                source_revision="abc123",
            )
        )
        reset = await adapter.reset(seed=29)
        action = await adapter.perform_action(ExecutionAction("attack", {"slot": 1}))
        checkpoint = await adapter.checkpoint()
        restored = await adapter.restore(checkpoint)
        verified = await adapter.verify_condition("player_hp > 0", expected=True)

        assert adapter.loaded.build_id == "build-42"
        assert reset.provenance is EvidenceProvenance.REPRODUCED
        assert action.state["last_action"] == "attack"
        assert restored.state["restored"] == "cp-1"
        assert verified.passed is True

    asyncio.run(exercise())


def test_execution_observation_rejects_synthetic_provenance():
    with pytest.raises(ValueError, match="reproduced"):
        ExecutionObservation(provenance=EvidenceProvenance.SYNTHETIC)


def test_execution_adapter_registry_rejects_duplicates_and_unknown_names():
    registry = GameExecutionAdapterRegistry()
    adapter = FakeAdapter()
    registry.register(adapter)

    assert registry.get("fake-runner") is adapter
    assert registry.describe() == [{"name": "fake-runner", "engine": "fake"}]

    with pytest.raises(ValueError, match="already registered"):
        registry.register(FakeAdapter())
    with pytest.raises(KeyError, match="unknown game execution adapter"):
        registry.get("missing")
