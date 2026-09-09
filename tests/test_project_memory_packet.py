from __future__ import annotations

from worldforge.context.project_job import (
    build_job_project_context,
    materialize_job_project_memory,
)
from worldforge.context.project_memory import ProjectMemoryStore
from worldforge.context.project_packet import (
    compile_project_memory_packet,
    resolve_project_scope,
)
from worldforge.product.store import ConversationStore, DEMO_USER_ID, DEMO_WORKSPACE_ID


def _setup(tmp_path):
    product = ConversationStore(
        tmp_path / "product.db",
        tmp_path / "assets",
        seed_dev_identity=True,
    )
    memory = ProjectMemoryStore(product.engine, auto_create_schema=True)
    project = memory.create_project(
        workspace_id=DEMO_WORKSPACE_ID,
        actor_id=DEMO_USER_ID,
        name="Atlas",
    )
    conversation = product.create_conversation(
        "Shield regression",
        "regression",
        workspace_id=DEMO_WORKSPACE_ID,
        created_by=DEMO_USER_ID,
    )
    memory.bind_conversation(
        workspace_id=DEMO_WORKSPACE_ID,
        actor_id=DEMO_USER_ID,
        project_id=project["id"],
        conversation_id=conversation["id"],
    )
    return memory, project, conversation


def _put(memory, project, *, content, source_id, build_ref=None):
    return memory.put_memory(
        workspace_id=DEMO_WORKSPACE_ID,
        actor_id=DEMO_USER_ID,
        project_id=project["id"],
        memory_key="combat.shield.cooldown",
        kind="fact",
        content=content,
        build_ref=build_ref,
        source_type="user",
        source_id=source_id,
        confidence=1.0,
        importance=0.9,
    )


def test_conflicting_selected_asset_builds_force_general_memory_only(tmp_path):
    memory, project, _conversation = _setup(tmp_path)
    general = _put(memory, project, content="默认护盾冷却 6 秒", source_id="general")
    scoped = _put(
        memory,
        project,
        content="build 1.4.7 护盾冷却 4 秒",
        source_id="147",
        build_ref="1.4.7",
    )
    scope = resolve_project_scope(
        [
            {"meta": {"build": "1.4.7"}},
            {"meta": {"build": "2.0.0"}},
        ]
    )
    assert scope.unresolved_conflict is True
    assert scope.retrieval_kwargs()["build_ref"] is None

    packet = compile_project_memory_packet(
        memory,
        workspace_id=DEMO_WORKSPACE_ID,
        actor_id=DEMO_USER_ID,
        project_id=project["id"],
        query="护盾冷却是多少",
        scope=scope,
    )
    ids = {row["id"] for row in packet.memories}
    assert general["id"] in ids
    assert scoped["id"] not in ids
    assert packet.stats()["project_memory_scope_conflict"] is True


def test_explicit_scope_is_stable_and_exposes_asset_disagreement(tmp_path):
    _memory, _project, _conversation = _setup(tmp_path)
    scope = resolve_project_scope(
        [{"meta": {"build": "2.0.0"}}],
        requested={"build_ref": "1.4.7", "branch_ref": "release"},
    )
    assert scope.build_ref == "1.4.7"
    assert scope.branch_ref == "release"
    assert scope.unresolved_conflict is False
    assert set(scope.conflicts["build_ref"]) == {"1.4.7", "2.0.0"}
    assert scope.retrieval_kwargs()["build_ref"] == "1.4.7"


def test_job_snapshot_contains_no_memory_content_and_invalidates_superseded_ref(tmp_path):
    memory, project, conversation = _setup(tmp_path)
    first = _put(memory, project, content="默认护盾冷却 6 秒", source_id="m1")
    job_context = build_job_project_context(
        memory,
        workspace_id=DEMO_WORKSPACE_ID,
        actor_id=DEMO_USER_ID,
        conversation_id=conversation["id"],
        query="护盾冷却",
    )
    assert job_context is not None
    snapshot_text = repr(job_context)
    assert "默认护盾冷却 6 秒" not in snapshot_text
    refs = job_context["memory_snapshot"]["memory_refs"]
    assert refs[0]["id"] == first["id"]
    assert refs[0]["revision"] == 1

    before = materialize_job_project_memory(
        memory,
        workspace_id=DEMO_WORKSPACE_ID,
        job_context=job_context,
    )
    assert before is not None
    assert before.memories[0]["id"] == first["id"]
    assert before.memories[0]["content"] == "默认护盾冷却 6 秒"

    second = _put(memory, project, content="默认护盾冷却 5 秒", source_id="m2")
    assert second["revision"] == 2

    after = materialize_job_project_memory(
        memory,
        workspace_id=DEMO_WORKSPACE_ID,
        job_context=job_context,
    )
    assert after is not None
    assert after.memories == ()
    assert after.invalidated_refs == 1
    # The old queued job must neither use stale v1 nor silently switch to v2.
    assert second["id"] not in after.stats()["project_memory_ids"]


def test_scope_without_identity_never_reads_build_specific_memory(tmp_path):
    memory, project, _conversation = _setup(tmp_path)
    scoped = _put(
        memory,
        project,
        content="build 1.4.7 护盾冷却 4 秒",
        source_id="147",
        build_ref="1.4.7",
    )
    packet = compile_project_memory_packet(
        memory,
        workspace_id=DEMO_WORKSPACE_ID,
        actor_id=DEMO_USER_ID,
        project_id=project["id"],
        query="护盾冷却",
        scope=resolve_project_scope([]),
    )
    assert scoped["id"] not in {row["id"] for row in packet.memories}


def test_retracted_after_enqueue_invalidates_ref_instead_of_falling_back(tmp_path):
    memory, project, conversation = _setup(tmp_path)
    scoped = _put(
        memory,
        project,
        content="build 1.4.7 护盾冷却 4 秒",
        source_id="147",
        build_ref="1.4.7",
    )
    job_context = build_job_project_context(
        memory,
        workspace_id=DEMO_WORKSPACE_ID,
        actor_id=DEMO_USER_ID,
        conversation_id=conversation["id"],
        query="护盾冷却",
        requested_scope={"build_ref": "1.4.7"},
    )
    assert job_context is not None
    assert job_context["memory_snapshot"]["memory_refs"][0]["id"] == scoped["id"]

    memory.set_memory_state(
        workspace_id=DEMO_WORKSPACE_ID,
        actor_id=DEMO_USER_ID,
        project_id=project["id"],
        memory_key="combat.shield.cooldown",
        state="retracted",
        build_ref="1.4.7",
        source_type="user",
        source_id="retract",
    )
    packet = materialize_job_project_memory(
        memory,
        workspace_id=DEMO_WORKSPACE_ID,
        job_context=job_context,
    )
    assert packet is not None
    assert packet.memories == ()
    assert packet.invalidated_refs == 1
