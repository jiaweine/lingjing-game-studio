import hashlib
import hmac
import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import update

from worldforge.product import ConversationStore
from worldforge.product.github_ci_webhook import build_github_ci_webhook_router
from worldforge.product.store import DEMO_USER_ID, DEMO_WORKSPACE_ID


SECRET = "webhook-test-secret"
HEAD_SHA = "a" * 40


def _signed_headers(delivery_id: str, body: bytes, *, event: str = "workflow_run"):
    digest = hmac.new(SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return {
        "X-Hub-Signature-256": f"sha256={digest}",
        "X-GitHub-Delivery": delivery_id,
        "X-GitHub-Event": event,
        "Content-Type": "application/json",
    }


def _fixture(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDFORGE_GITHUB_WEBHOOK_SECRET", SECRET)
    store = ConversationStore(
        db_path=tmp_path / "product.db",
        asset_dir=tmp_path / "assets",
    )
    conversation = store.create_conversation(
        title="Boss CI 重验",
        workspace_id=DEMO_WORKSPACE_ID,
        created_by=DEMO_USER_ID,
    )
    link = store.link_github_issue(
        conversation["id"],
        workspace_id=DEMO_WORKSPACE_ID,
        created_by=DEMO_USER_ID,
        repository="owner/game",
        issue_number=7,
    )
    store.record_github_code_context(
        link["id"],
        conversation_id=conversation["id"],
        workspace_id=DEMO_WORKSPACE_ID,
        pull_request={
            "number": 12,
            "title": "Fix boss",
            "url": "https://github.com/owner/game/pull/12",
            "state": "open",
            "merged": False,
            "head_sha": HEAD_SHA,
            "head_ref": "fix/boss",
            "base_sha": "b" * 40,
            "base_ref": "main",
        },
    )
    source = store.enqueue_job(
        workspace_id=DEMO_WORKSPACE_ID,
        conversation_id=conversation["id"],
        payload={
            "text": "复现并验证 Boss 二阶段问题",
            "provider": "demo",
            "asset_ids": [],
            "project_context": {
                "actor_id": DEMO_USER_ID,
                "project_id": "project-demo",
                "scope": {"commit_ref": "old"},
                "memory_snapshot": {"scope": {"commit_ref": "old"}, "memory_refs": []},
            },
        },
    )
    with store.engine.begin() as connection:
        connection.execute(
            update(store.jobs)
            .where(store.jobs.c.id == source["id"])
            .values(status="completed", completed_at=time.time())
        )

    scheduled = []

    async def schedule_retry(job, background_tasks, principal):
        scheduled.append((job, principal))

    app = FastAPI()
    app.include_router(
        build_github_ci_webhook_router(store=store, schedule_retry=schedule_retry)
    )
    return TestClient(app), store, conversation, scheduled


def _workflow_payload(*, conclusion="success", head_sha=HEAD_SHA):
    return {
        "action": "completed",
        "repository": {"full_name": "Owner/Game"},
        "workflow_run": {
            "id": 991,
            "name": "Game CI",
            "html_url": "https://github.com/owner/game/actions/runs/991",
            "head_sha": head_sha,
            "conclusion": conclusion,
        },
    }


def test_signed_successful_workflow_enqueues_exact_commit_revalidation(tmp_path, monkeypatch):
    client, store, conversation, scheduled = _fixture(tmp_path, monkeypatch)
    body = json.dumps(_workflow_payload(), separators=(",", ":")).encode("utf-8")
    response = client.post(
        "/integrations/github/webhook",
        content=body,
        headers=_signed_headers("delivery-1", body),
    )

    assert response.status_code == 200
    assert response.json()["matched"] == 1
    assert response.json()["enqueued"] == 1
    assert len(scheduled) == 1
    job, principal = scheduled[0]
    assert principal.user_id == DEMO_USER_ID
    assert job["payload"]["ci_trigger"]["head_sha"] == HEAD_SHA
    assert f"Commit={HEAD_SHA}" in job["payload"]["text"]
    assert "CI 成功本身不代表问题已修复" in job["payload"]["text"]
    assert job["payload"]["project_context"]["scope"]["commit_ref"] == HEAD_SHA
    assert (
        job["payload"]["project_context"]["memory_snapshot"]["scope"]["commit_ref"]
        == HEAD_SHA
    )

    delivery = store.get_github_webhook_delivery("delivery-1")
    assert delivery["status"] == "enqueued"
    assert delivery["matched_count"] == 1
    assert delivery["enqueued_count"] == 1
    events = store.list_events(conversation["id"], workspace_id=DEMO_WORKSPACE_ID)
    assert any(event["type"] == "ci.revalidation.queued" for event in events)


def test_webhook_delivery_is_replay_safe(tmp_path, monkeypatch):
    client, store, _conversation, scheduled = _fixture(tmp_path, monkeypatch)
    body = json.dumps(_workflow_payload(), separators=(",", ":")).encode("utf-8")
    headers = _signed_headers("delivery-repeat", body)

    first = client.post("/integrations/github/webhook", content=body, headers=headers)
    second = client.post("/integrations/github/webhook", content=body, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert len(scheduled) == 1
    assert store.get_github_webhook_delivery("delivery-repeat")["enqueued_count"] == 1


def test_webhook_rejects_bad_signature_before_delivery_is_recorded(tmp_path, monkeypatch):
    client, store, _conversation, scheduled = _fixture(tmp_path, monkeypatch)
    body = json.dumps(_workflow_payload()).encode("utf-8")
    response = client.post(
        "/integrations/github/webhook",
        content=body,
        headers={
            "X-Hub-Signature-256": "sha256=deadbeef",
            "X-GitHub-Delivery": "delivery-bad-signature",
            "X-GitHub-Event": "workflow_run",
        },
    )

    assert response.status_code == 401
    assert scheduled == []
    with pytest.raises(KeyError):
        store.get_github_webhook_delivery("delivery-bad-signature")


def test_failed_workflow_is_recorded_but_does_not_enqueue(tmp_path, monkeypatch):
    client, store, _conversation, scheduled = _fixture(tmp_path, monkeypatch)
    body = json.dumps(_workflow_payload(conclusion="failure")).encode("utf-8")
    response = client.post(
        "/integrations/github/webhook",
        content=body,
        headers=_signed_headers("delivery-failed", body),
    )

    assert response.status_code == 200
    assert response.json()["ignored"] == "result"
    assert scheduled == []
    assert store.get_github_webhook_delivery("delivery-failed")["status"] == "ignored_result"
