from __future__ import annotations

import pytest

from worldforge.context.memory_consolidator import MemoryConsolidator
from worldforge.context.project_memory import ProjectMemoryStore
from worldforge.context.project_packet import ProjectScopeSnapshot
from worldforge.product.store import ConversationStore, DEMO_USER_ID, DEMO_WORKSPACE_ID


def _setup(tmp_path):
    product = ConversationStore(
        tmp_path / "product.db",
        tmp_path / "assets",
        seed_dev_identity=True,
    )
    memory = ProjectMemoryStore(product.engine, auto_create_schema=True)
    consolidator = MemoryConsolidator(
        product.engine,
        memory,
        auto_create_schema=True,
    )
    project = memory.create_project(
        workspace_id=DEMO_WORKSPACE_ID,
        actor_id=DEMO_USER_ID,
        name="Atlas",
    )
    conversation = product.create_conversation(
        "Release rules",
        "general",
        workspace_id=DEMO_WORKSPACE_ID,
        created_by=DEMO_USER_ID,
    )
    memory.bind_conversation(
        workspace_id=DEMO_WORKSPACE_ID,
        actor_id=DEMO_USER_ID,
        project_id=project["id"],
        conversation_id=conversation["id"],
    )
    return product, memory, consolidator, project, conversation


def _propose(product, consolidator, project, conversation, content, *, role="user", scope=None):
    message = product.add_message(
        conversation["id"],
        role,
        content,
        {},
        workspace_id=DEMO_WORKSPACE_ID,
    )
    proposals = consolidator.propose_user_message(
        workspace_id=DEMO_WORKSPACE_ID,
        actor_id=DEMO_USER_ID,
        project_id=project["id"],
        conversation_id=conversation["id"],
        message_id=message["id"],
        content=content,
        scope=scope or ProjectScopeSnapshot(),
    )
    return message, proposals


def test_extractor_prefers_precision_and_skips_questions_uncertainty_and_task_commands(tmp_path):
    product, _memory, consolidator, project, conversation = _setup(tmp_path)
    content = (
        "发布前必须运行全套回归。"
        "是不是要把护盾改成 4 秒？"
        "这个问题可能是冷却导致。"
        "Use logs to check this. "
        "我们决定采用方案 B。"
        "已确认护盾冷却是 4 秒。"
    )
    _message, proposals = _propose(
        product, consolidator, project, conversation, content
    )

    assert {row["kind"] for row in proposals} == {"constraint", "decision", "fact"}
    text = "\n".join(row["content"] for row in proposals)
    assert "发布前必须运行全套回归" in text
    assert "决定采用方案 B" in text
    assert "已确认护盾冷却是 4 秒" in text
    assert "是不是要把护盾改成 4 秒" not in text
    assert "可能是冷却" not in text
    assert "Use logs to check this" not in text


def test_proposal_source_must_be_authoritative_user_message(tmp_path):
    product, _memory, consolidator, project, conversation = _setup(tmp_path)
    assistant = product.add_message(
        conversation["id"],
        "assistant",
        "已确认护盾冷却是 4 秒。",
        {},
        workspace_id=DEMO_WORKSPACE_ID,
    )
    with pytest.raises(ValueError, match="只能来自权威 user message"):
        consolidator.propose_user_message(
            workspace_id=DEMO_WORKSPACE_ID,
            actor_id=DEMO_USER_ID,
            project_id=project["id"],
            conversation_id=conversation["id"],
            message_id=assistant["id"],
            content=assistant["content"],
            scope=ProjectScopeSnapshot(),
        )

    user = product.add_message(
        conversation["id"],
        "user",
        "发布前必须运行回归。",
        {},
        workspace_id=DEMO_WORKSPACE_ID,
    )
    with pytest.raises(ValueError, match="完全一致"):
        consolidator.propose_user_message(
            workspace_id=DEMO_WORKSPACE_ID,
            actor_id=DEMO_USER_ID,
            project_id=project["id"],
            conversation_id=conversation["id"],
            message_id=user["id"],
            content="发布前必须跳过回归。",
            scope=ProjectScopeSnapshot(),
        )


def test_approval_is_atomic_idempotent_and_preserves_scope_and_provenance(tmp_path):
    product, memory, consolidator, project, conversation = _setup(tmp_path)
    scope = ProjectScopeSnapshot(build_ref="1.4.7", branch_ref="release")
    _message, proposals = _propose(
        product,
        consolidator,
        project,
        conversation,
        "发布前必须运行全套回归。",
        scope=scope,
    )
    assert len(proposals) == 1
    proposal = proposals[0]

    approved = consolidator.approve_proposal(
        workspace_id=DEMO_WORKSPACE_ID,
        actor_id=DEMO_USER_ID,
        project_id=project["id"],
        proposal_id=proposal["id"],
        memory_key="release.regression.required",
        note="团队确认长期有效",
    )
    memory_row = approved["memory"]
    assert approved["proposal"]["status"] == "approved"
    assert memory_row["revision"] == 1
    assert memory_row["source_type"] == "user_confirmed"
    assert memory_row["source_id"] == f"proposal:{proposal['id']}"
    assert memory_row["build_ref"] == "1.4.7"
    assert memory_row["branch_ref"] == "release"

    repeated = consolidator.approve_proposal(
        workspace_id=DEMO_WORKSPACE_ID,
        actor_id=DEMO_USER_ID,
        project_id=project["id"],
        proposal_id=proposal["id"],
        memory_key="ignored.on.idempotent.replay",
    )
    assert repeated["memory"]["id"] == memory_row["id"]
    history = memory.memory_history(
        workspace_id=DEMO_WORKSPACE_ID,
        actor_id=DEMO_USER_ID,
        project_id=project["id"],
        memory_key="release.regression.required",
        build_ref="1.4.7",
        branch_ref="release",
    )
    assert len(history) == 1


def test_rejected_proposal_cannot_later_be_promoted(tmp_path):
    product, _memory, consolidator, project, conversation = _setup(tmp_path)
    _message, proposals = _propose(
        product,
        consolidator,
        project,
        conversation,
        "我们决定采用方案 B。",
    )
    proposal = proposals[0]
    rejected = consolidator.reject_proposal(
        workspace_id=DEMO_WORKSPACE_ID,
        actor_id=DEMO_USER_ID,
        project_id=project["id"],
        proposal_id=proposal["id"],
        note="只针对当前实验，不进入长期记忆",
    )
    assert rejected["status"] == "rejected"
    with pytest.raises(ValueError, match="只有 pending proposal"):
        consolidator.approve_proposal(
            workspace_id=DEMO_WORKSPACE_ID,
            actor_id=DEMO_USER_ID,
            project_id=project["id"],
            proposal_id=proposal["id"],
        )


def test_two_confirmed_updates_become_ordered_memory_revisions(tmp_path):
    product, memory, consolidator, project, conversation = _setup(tmp_path)
    _, first_proposals = _propose(
        product,
        consolidator,
        project,
        conversation,
        "已确认护盾冷却是 6 秒。",
    )
    first = consolidator.approve_proposal(
        workspace_id=DEMO_WORKSPACE_ID,
        actor_id=DEMO_USER_ID,
        project_id=project["id"],
        proposal_id=first_proposals[0]["id"],
        memory_key="combat.shield.cooldown",
    )["memory"]

    _, second_proposals = _propose(
        product,
        consolidator,
        project,
        conversation,
        "已确认护盾冷却是 5 秒。",
    )
    second = consolidator.approve_proposal(
        workspace_id=DEMO_WORKSPACE_ID,
        actor_id=DEMO_USER_ID,
        project_id=project["id"],
        proposal_id=second_proposals[0]["id"],
        memory_key="combat.shield.cooldown",
    )["memory"]

    assert first["revision"] == 1
    assert second["revision"] == 2
    assert second["supersedes_id"] == first["id"]
    current = memory.current_memory(
        workspace_id=DEMO_WORKSPACE_ID,
        actor_id=DEMO_USER_ID,
        project_id=project["id"],
        memory_key="combat.shield.cooldown",
    )
    assert current["id"] == second["id"]
    assert current["content"] == "已确认护盾冷却是 5 秒。"
