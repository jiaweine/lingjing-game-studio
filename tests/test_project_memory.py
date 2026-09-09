from __future__ import annotations

import pytest

from worldforge.context.project_memory import ProjectMemoryStore
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
        name="Project Atlas",
        default_branch="main",
    )
    conversation = product.create_conversation(
        "Boss shield regression",
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
    return product, memory, project, conversation


def test_project_binding_is_explicit_and_workspace_isolated(tmp_path):
    product, memory, project, conversation = _setup(tmp_path)
    resolved = memory.project_for_conversation(
        workspace_id=DEMO_WORKSPACE_ID,
        actor_id=DEMO_USER_ID,
        conversation_id=conversation["id"],
    )
    assert resolved is not None
    assert resolved["id"] == project["id"]

    other = product.create_user_workspace(
        email="other@example.com",
        name="Other",
        password_hash="!test",
        workspace_name="Other Workspace",
    )
    with pytest.raises(KeyError):
        memory.get_project(
            workspace_id=other["workspace_id"],
            actor_id=other["user_id"],
            project_id=project["id"],
        )

    foreign_conversation = product.create_conversation(
        "Foreign",
        "general",
        workspace_id=other["workspace_id"],
        created_by=other["user_id"],
    )
    with pytest.raises(PermissionError):
        memory.bind_conversation(
            workspace_id=DEMO_WORKSPACE_ID,
            actor_id=DEMO_USER_ID,
            project_id=project["id"],
            conversation_id=foreign_conversation["id"],
        )


def test_same_memory_key_has_independent_heads_per_build(tmp_path):
    _product, memory, project, _conversation = _setup(tmp_path)
    common = dict(
        workspace_id=DEMO_WORKSPACE_ID,
        actor_id=DEMO_USER_ID,
        project_id=project["id"],
        memory_key="combat.shield.cooldown",
        kind="fact",
        source_type="user",
    )

    general_v1 = memory.put_memory(
        **common,
        content="默认护盾冷却是 6 秒",
        value={"seconds": 6},
        source_id="m-general-1",
    )
    general_v2 = memory.put_memory(
        **common,
        content="默认护盾冷却是 5 秒",
        value={"seconds": 5},
        source_id="m-general-2",
    )
    build_147 = memory.put_memory(
        **common,
        content="build 1.4.7 护盾冷却是 4 秒",
        value={"seconds": 4},
        build_ref="1.4.7",
        source_id="m-147",
    )
    build_200 = memory.put_memory(
        **common,
        content="build 2.0.0 护盾冷却是 7 秒",
        value={"seconds": 7},
        build_ref="2.0.0",
        source_id="m-200",
    )

    assert general_v1["revision"] == 1
    assert general_v2["revision"] == 2
    assert general_v2["supersedes_id"] == general_v1["id"]
    assert build_147["revision"] == 1
    assert build_200["revision"] == 1
    assert build_147["scope_key"] != build_200["scope_key"]

    no_identity = memory.list_current_memories(
        workspace_id=DEMO_WORKSPACE_ID,
        actor_id=DEMO_USER_ID,
        project_id=project["id"],
    )
    assert [row["value"]["seconds"] for row in no_identity] == [5]

    for build, expected in (("1.4.7", 4), ("2.0.0", 7)):
        rows = memory.list_current_memories(
            workspace_id=DEMO_WORKSPACE_ID,
            actor_id=DEMO_USER_ID,
            project_id=project["id"],
            build_ref=build,
        )
        assert len(rows) == 1
        assert rows[0]["value"]["seconds"] == expected
        assert rows[0]["build_ref"] == build
        assert rows[0]["scope_specificity"] > 0

    history = memory.memory_history(
        workspace_id=DEMO_WORKSPACE_ID,
        actor_id=DEMO_USER_ID,
        project_id=project["id"],
        memory_key="combat.shield.cooldown",
    )
    assert [row["revision"] for row in history] == [1, 2]
    assert [row["source_id"] for row in history] == ["m-general-1", "m-general-2"]


def test_scoped_retraction_is_tombstone_and_does_not_fall_back_to_general(tmp_path):
    _product, memory, project, _conversation = _setup(tmp_path)
    common = dict(
        workspace_id=DEMO_WORKSPACE_ID,
        actor_id=DEMO_USER_ID,
        project_id=project["id"],
        memory_key="combat.damage.coefficient",
        kind="constraint",
        source_type="user",
    )
    memory.put_memory(
        **common,
        content="默认 damage_coefficient 必须保持 1.0",
        value={"value": 1.0},
        source_id="m-general",
    )
    scoped = memory.put_memory(
        **common,
        content="build 1.4.7 damage_coefficient 必须保持 0.8",
        value={"value": 0.8},
        build_ref="1.4.7",
        source_id="m-scoped",
    )
    assert scoped["state"] == "active"

    memory.set_memory_state(
        workspace_id=DEMO_WORKSPACE_ID,
        actor_id=DEMO_USER_ID,
        project_id=project["id"],
        memory_key="combat.damage.coefficient",
        build_ref="1.4.7",
        state="retracted",
        source_type="user",
        source_id="m-retract",
    )

    # Retraction at the winning scope blocks the general value from resurfacing.
    active = memory.list_current_memories(
        workspace_id=DEMO_WORKSPACE_ID,
        actor_id=DEMO_USER_ID,
        project_id=project["id"],
        build_ref="1.4.7",
    )
    assert active == []

    governed = memory.list_current_memories(
        workspace_id=DEMO_WORKSPACE_ID,
        actor_id=DEMO_USER_ID,
        project_id=project["id"],
        build_ref="1.4.7",
        include_nonactive=True,
    )
    assert len(governed) == 1
    assert governed[0]["state"] == "retracted"
    assert governed[0]["scope_specificity"] > 0

    # Another build with no scoped tombstone may still use the general memory.
    other_build = memory.list_current_memories(
        workspace_id=DEMO_WORKSPACE_ID,
        actor_id=DEMO_USER_ID,
        project_id=project["id"],
        build_ref="2.0.0",
    )
    assert len(other_build) == 1
    assert other_build[0]["value"]["value"] == 1.0


def test_search_respects_identity_expiry_and_provenance(tmp_path):
    _product, memory, project, conversation = _setup(tmp_path)
    now = 2_000_000_000.0
    memory.put_memory(
        workspace_id=DEMO_WORKSPACE_ID,
        actor_id=DEMO_USER_ID,
        project_id=project["id"],
        memory_key="render.frame_budget",
        kind="constraint",
        content="release 分支 frame_budget 必须保持 8",
        value={"frame_budget": 8},
        branch_ref="release",
        source_type="message",
        source_id="message-42",
        source_excerpt="不要修改 frame_budget=8",
        confidence=1.0,
        importance=0.9,
        pinned=True,
        valid_from=now - 100,
        valid_to=now + 100,
    )
    memory.put_memory(
        workspace_id=DEMO_WORKSPACE_ID,
        actor_id=DEMO_USER_ID,
        project_id=project["id"],
        memory_key="obsolete.render.note",
        kind="gotcha",
        content="过期的 render_fence 提示",
        source_type="message",
        source_id="message-old",
        expires_at=now - 1,
    )

    # Store time filtering is deterministic; use list_current_memories for an explicit time.
    scoped = memory.list_current_memories(
        workspace_id=DEMO_WORKSPACE_ID,
        actor_id=DEMO_USER_ID,
        project_id=project["id"],
        branch_ref="release",
        at_time=now,
    )
    assert len(scoped) == 1
    row = scoped[0]
    assert row["memory_key"] == "render.frame_budget"
    assert row["source_id"] == "message-42"
    assert row["source_excerpt"] == "不要修改 frame_budget=8"

    # Without branch identity the branch-scoped memory is not eligible at all.
    assert memory.list_current_memories(
        workspace_id=DEMO_WORKSPACE_ID,
        actor_id=DEMO_USER_ID,
        project_id=project["id"],
        at_time=now,
    ) == []

    # Usage is separately auditable rather than mutating semantic memory importance.
    memory.record_usage(
        workspace_id=DEMO_WORKSPACE_ID,
        actor_id=DEMO_USER_ID,
        project_id=project["id"],
        memory_id=row["id"],
        conversation_id=conversation["id"],
        reason="context-pack",
        score=0.91,
    )
