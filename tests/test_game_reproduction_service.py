import asyncio

import pytest

from worldforge.integrations.game_execution import (
    ExecutionAction,
    ExecutionCheckpoint,
    ExecutionObservation,
    GameBuildRef,
    GameExecutionAdapter,
    VerificationResult,
)
from worldforge.integrations.reproduction import (
    AssertionSpec,
    GameReproductionService,
    ReproductionRequest,
)


class RecordingAdapter(GameExecutionAdapter):
    def __init__(self, *, failing_action=None, verification_passed=True):
        self.loaded = None
        self.calls = []
        self.closed = False
        self.failing_action = failing_action
        self.verification_passed = verification_passed

    @property
    def name(self):
        return "unity-local"

    @property
    def engine(self):
        return "unity"

    async def load_build(self, build):
        self.loaded = build
        self.calls.append(("load_build", build.build_id))

    async def reset(self, *, seed=None):
        self.calls.append(("reset", seed))
        return ExecutionObservation(state={"seed": seed, "phase": "baseline"})

    async def perform_action(self, action):
        self.calls.append(("action", action.name))
        if action.name == self.failing_action:
            raise RuntimeError("runner action failed")
        return ExecutionObservation(state={"last_action": action.name})

    async def observe(self):
        self.calls.append(("observe", None))
        return ExecutionObservation(state={"phase": "final"})

    async def checkpoint(self):
        self.calls.append(("checkpoint", None))
        return ExecutionCheckpoint("baseline-cp")

    async def restore(self, checkpoint):
        self.calls.append(("restore", checkpoint.checkpoint_id))
        return ExecutionObservation(state={"restored": checkpoint.checkpoint_id})

    async def verify_condition(self, assertion, *, expected=None):
        self.calls.append(("verify", assertion))
        return VerificationResult(
            self.verification_passed,
            assertion,
            {"expected": expected},
        )

    async def close(self):
        self.closed = True
        self.calls.append(("close", None))


def _request(*, assertions=True):
    return ReproductionRequest(
        build=GameBuildRef(
            build_id="unity-build-42",
            engine="unity",
            version="1.4.0",
            source_revision="abc123",
        ),
        seed=29,
        actions=(
            ExecutionAction("move", {"x": 2}),
            ExecutionAction("attack", {"slot": 1}),
        ),
        assertions=(
            (AssertionSpec("boss_hp < 50", expected=True),)
            if assertions else ()
        ),
    )


def test_reproduction_service_preserves_build_action_and_assertion_provenance():
    async def exercise():
        adapter = RecordingAdapter()
        result = await GameReproductionService().run(adapter, _request())

        assert result.claim_status == "verified"
        assert result.all_assertions_passed is True
        assert [row.stage for row in result.observations] == [
            "baseline", "action", "action", "final"
        ]
        assert result.observations[1].action.name == "move"
        assert result.observations[2].action_index == 1
        assert adapter.closed is True

        context = result.evidence_context()
        assert context == {
            "provenance": "reproduced",
            "claim_status": "verified",
            "adapter": "unity-local",
            "engine": "unity",
            "build_id": "unity-build-42",
            "build_version": "1.4.0",
            "source_revision": "abc123",
            "seed": 29,
            "action_count": 2,
            "action_trace": [
                {"index": 0, "name": "move", "arguments": {"x": 2}},
                {"index": 1, "name": "attack", "arguments": {"slot": 1}},
            ],
            "assertion_count": 1,
            "assertions_passed": 1,
            "assertion_results": [
                {
                    "assertion": "boss_hp < 50",
                    "expected": True,
                    "passed": True,
                    "details": {"expected": True},
                }
            ],
            "checkpoint_id": "baseline-cp",
        }

    asyncio.run(exercise())


def test_real_runtime_evidence_without_assertions_does_not_claim_verified_reproduction():
    async def exercise():
        result = await GameReproductionService().run(
            RecordingAdapter(), _request(assertions=False)
        )
        assert result.claim_status == "executed_not_verified"
        assert result.all_assertions_passed is False
        assert result.evidence_context()["assertion_results"] == []

    asyncio.run(exercise())


def test_failed_assertion_does_not_claim_issue_was_reproduced():
    async def exercise():
        result = await GameReproductionService().run(
            RecordingAdapter(verification_passed=False), _request()
        )
        assert result.claim_status == "not_reproduced"
        context = result.evidence_context()
        assert context["assertions_passed"] == 0
        assert context["assertion_results"][0]["passed"] is False

    asyncio.run(exercise())


def test_adapter_is_closed_when_execution_fails():
    async def exercise():
        adapter = RecordingAdapter(failing_action="attack")
        with pytest.raises(RuntimeError, match="runner action failed"):
            await GameReproductionService().run(adapter, _request())
        assert adapter.closed is True

    asyncio.run(exercise())


def test_adapter_engine_must_match_build_engine():
    async def exercise():
        adapter = RecordingAdapter()
        request = ReproductionRequest(
            build=GameBuildRef(build_id="build-1", engine="unreal")
        )
        with pytest.raises(ValueError, match="does not match"):
            await GameReproductionService().run(adapter, request)
        assert adapter.loaded is None

    asyncio.run(exercise())
