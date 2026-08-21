from dataclasses import dataclass

from worldforge.envs import get_scenario
from worldforge.models import RunConfig
from worldforge.runtime.harness_genome import HarnessGenomeStore
from worldforge.runtime.memory import EpisodicMemory, OutcomeRecord
from worldforge.runtime.policy import WorldForgePolicy
from worldforge.runtime.provenance import (
    build_runtime_provenance,
    source_revision,
    stable_digest,
)
from worldforge.runtime.skill_bank import SkillBank
from worldforge.runtime.verifier import StateVerifier


class FakeKernel:
    pass


@dataclass
class SimpleValue:
    beta: int
    alpha: str


def _build(*, policy=None, memory=None, session_meta=None, environ=None):
    scenario = get_scenario("boss_burst")
    return build_runtime_provenance(
        kernel=FakeKernel(),
        policy=policy or WorldForgePolicy(),
        harness_genome=HarnessGenomeStore.current(),
        skill_bank=SkillBank(),
        memory=memory or EpisodicMemory(),
        verifier=StateVerifier(),
        scenario=scenario.model_dump(),
        config=RunConfig(scenario_id="boss_burst", seed=29).model_dump(),
        session_meta=session_meta,
        environ=environ or {},
    )


def test_stable_digest_is_order_independent_for_mapping_keys():
    assert stable_digest({"b": 2, "a": 1}) == stable_digest({"a": 1, "b": 2})
    assert stable_digest(SimpleValue(beta=2, alpha="x")) == stable_digest(
        {"alpha": "x", "beta": 2}
    )


def test_runtime_provenance_is_stable_for_same_execution_components():
    policy = WorldForgePolicy()
    memory = EpisodicMemory()

    first = _build(policy=policy, memory=memory, environ={"GITHUB_SHA": "abc123"})
    second = _build(policy=policy.clone(), memory=memory, environ={"GITHUB_SHA": "abc123"})

    assert first == second
    assert first["source_revision"] == "abc123"
    assert len(first["combined_fingerprint"]) == 64
    assert len(first["policy"]["fingerprint"]) == 64
    assert first["harness"]["genome_id"]


def test_policy_weight_change_changes_policy_and_combined_fingerprints():
    baseline_policy = WorldForgePolicy()
    mutated_policy = baseline_policy.clone()
    mutated_policy.W1[0, 0] += 0.001

    baseline = _build(policy=baseline_policy)
    mutated = _build(policy=mutated_policy)

    assert baseline["policy"]["generation"] == mutated["policy"]["generation"]
    assert baseline["policy"]["fingerprint"] != mutated["policy"]["fingerprint"]
    assert baseline["combined_fingerprint"] != mutated["combined_fingerprint"]


def test_memory_change_changes_memory_fingerprint():
    empty = EpisodicMemory()
    populated = EpisodicMemory()
    populated.add(OutcomeRecord("boss_burst", "{}", "attack", 3.5, True))

    before = _build(memory=empty)
    after = _build(memory=populated)

    assert before["memory"]["record_count"] == 0
    assert after["memory"]["record_count"] == 1
    assert before["memory"]["fingerprint"] != after["memory"]["fingerprint"]


def test_provenance_keeps_only_version_metadata_not_arbitrary_session_data():
    provenance = _build(
        session_meta={
            "game_build_id": "build-42",
            "game_build_version": "1.4.0",
            "dataset_version": "regression-corpus-v3",
            "eval_version": "qa-eval-v2",
            "model_version": "provider-model-v7",
            "workspace_id": "secret-workspace",
            "user_id": "secret-user",
            "prompt": "do not persist this",
        }
    )

    assert provenance["versions"] == {
        "game_build_version": "1.4.0",
        "game_build_id": "build-42",
        "dataset_version": "regression-corpus-v3",
        "eval_version": "qa-eval-v2",
        "model_version": "provider-model-v7",
    }
    serialized = repr(provenance)
    assert "secret-workspace" not in serialized
    assert "secret-user" not in serialized
    assert "do not persist this" not in serialized


def test_explicit_source_revision_wins_over_environment():
    assert source_revision(
        "explicit-sha",
        environ={"WORLD_FORGE_SOURCE_REVISION": "env-sha", "GITHUB_SHA": "github-sha"},
    ) == "explicit-sha"
    assert source_revision(
        environ={"WORLD_FORGE_SOURCE_REVISION": "env-sha", "GITHUB_SHA": "github-sha"},
    ) == "env-sha"
