import time

from sqlalchemy import update

from worldforge.product import ConversationStore
from worldforge.product.store import DEMO_USER_ID, DEMO_WORKSPACE_ID


def _trigger(delivery_id: str, sha: str) -> dict:
    return {
        "source": "github_workflow_run",
        "delivery_id": delivery_id,
        "repository": "owner/game",
        "head_sha": sha,
        "workflow_run_id": delivery_id,
        "workflow_name": "Game CI",
        "workflow_url": f"https://github.com/owner/game/actions/runs/{delivery_id}",
        "conclusion": "success",
    }


def test_ci_result_persists_authoritative_scope_and_repeated_prompts_do_not_accumulate(tmp_path):
    store = ConversationStore(
        db_path=tmp_path / "product.db",
        asset_dir=tmp_path / "assets",
    )
    conversation = store.create_conversation(
        title="连续 CI 重验",
        workspace_id=DEMO_WORKSPACE_ID,
        created_by=DEMO_USER_ID,
    )
    original_goal = "验证 Boss 二阶段修复 " + ("x" * 10_500)
    source = store.enqueue_job(
        workspace_id=DEMO_WORKSPACE_ID,
        conversation_id=conversation["id"],
        payload={
            "text": original_goal + "\n【验证范围】Commit=old-commit",
            "provider": "demo",
            "asset_ids": [],
            "project_context": {
                "actor_id": DEMO_USER_ID,
                "project_id": "project-demo",
                "scope": {
                    "build_ref": "build-42",
                    "branch_ref": "fix/boss",
                    "commit_ref": "old-commit",
                    "environment_ref": "qa",
                },
                "memory_snapshot": {
                    "scope": {"commit_ref": "old-commit"},
                    "memory_refs": [{"id": "old", "revision": 1}],
                },
            },
        },
    )
    with store.engine.begin() as connection:
        connection.execute(
            update(store.jobs)
            .where(store.jobs.c.id == source["id"])
            .values(status="completed", completed_at=time.time())
        )

    first_sha = "d" * 40
    first = store.enqueue_ci_revalidation(
        workspace_id=DEMO_WORKSPACE_ID,
        conversation_id=conversation["id"],
        trigger=_trigger("delivery-first", first_sha),
    )
    assert len(first["payload"]["text"]) <= 12_000
    assert first["payload"]["text"].count("【验证范围】") == 1
    assert first["payload"]["text"].count("【CI 自动重验】") == 1
    assert f"Commit={first_sha}" in first["payload"]["text"]
    assert "Build=build-42" in first["payload"]["text"]
    assert "Branch=fix/boss" in first["payload"]["text"]
    assert first["payload"]["project_context"]["memory_snapshot"]["memory_refs"] == []

    with store.engine.begin() as connection:
        connection.execute(
            update(store.jobs)
            .where(store.jobs.c.id == first["id"])
            .values(status="running", started_at=time.time())
        )
    message = store.complete_job_answer(
        first["id"],
        workspace_id=DEMO_WORKSPACE_ID,
        content="修复后未复现原问题。",
        payload={
            "outcome": {
                "issue_lifecycle": True,
                "requires_project_verification": True,
                "verified": True,
                "state": "verified",
                "label": "已验证",
            },
            "evidence": [],
            "context": {"provider": "demo"},
        },
    )
    assert message is not None
    result_context = message["payload"]["context"]
    assert result_context["provider"] == "demo"
    assert result_context["ci_trigger"]["delivery_id"] == "delivery-first"
    assert result_context["verification_scope"] == {
        "build_ref": "build-42",
        "branch_ref": "fix/boss",
        "commit_ref": first_sha,
        "environment_ref": "qa",
    }

    second_sha = "e" * 40
    second = store.enqueue_ci_revalidation(
        workspace_id=DEMO_WORKSPACE_ID,
        conversation_id=conversation["id"],
        trigger=_trigger("delivery-second", second_sha),
    )
    text = second["payload"]["text"]
    assert text.count("【验证范围】") == 1
    assert text.count("【CI 自动重验】") == 1
    assert f"Commit={second_sha}" in text
    assert f"Commit={first_sha}" not in text
    assert "old-commit" not in text
    assert second["payload"]["ci_base_text"] == first["payload"]["ci_base_text"]
