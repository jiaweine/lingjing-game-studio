import hashlib
import hmac
import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, update

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
    monkeypatch.setenv("WORLDFORGE_GITHUB_REVALIDATION_WORKFLOWS", "Game CI")
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
                "memory_snapshot": {
                    "scope": {"commit_ref": "old"},
                    "memory_refs": [
                        {"id": "old-memory", "revision": 1, "retrieval_score": 0.9}
                    ],
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

    scheduled = []

    async def schedule_retry(job, background_tasks, principal):
        scheduled.append((job, principal))

    app = FastAPI()
    app.include_router(
        build_github_ci_webhook_router(store=store, schedule_retry=schedule_retry)
    )
    return TestClient(app), store, conversation, link, scheduled


def _workflow_payload(*, conclusion="success", head_sha=HEAD_SHA, workflow_name="Game CI"):
    return {
        "action": "completed",
        "repository": {"full_name": "Owner/Game"},
        "workflow_run": {
            "id": 991,
            "name": workflow_name,
            "html_url": "https://github.com/owner/game/actions/runs/991",
            "head_sha": head_sha,
            "conclusion": conclusion,
        },
    }


def _trigger(delivery_id: str) -> dict:
    return {
        "source": "github_workflow_run",
        "delivery_id": delivery_id,
        "repository": "owner/game",
        "head_sha": HEAD_SHA,
        "workflow_run_id": "991",
        "workflow_name": "Game CI",
        "workflow_url": "https://github.com/owner/game/actions/runs/991",
        "conclusion": "success",
    }


def _job_ids(store, conversation_id: str) -> list[str]:
    with store.engine.connect() as connection:
        rows = connection.execute(
            select(store.jobs.c.id)
            .where(store.jobs.c.conversation_id == conversation_id)
            .order_by(store.jobs.c.created_at, store.jobs.c.id)
        ).fetchall()
    return [str(row[0]) for row in rows]


def test_signed_successful_workflow_enqueues_exact_commit_revalidation(tmp_path, monkeypatch):
    client, store, conversation, _link, scheduled = _fixture(tmp_path, monkeypatch)
    body = json.dumps(_workflow_payload(), separators=(",", ":")).encode("utf-8")
    response = client.post(
        "/integrations/github/webhook",
        content=body,
        headers=_signed_headers("delivery-1", body),
    )

    assert response.status_code == 200
    assert response.json()["matched"] == 1
    assert response.json()["enqueued"] == 1
    assert response.json()["stale"] == 0
    assert len(scheduled) == 1
    job, principal = scheduled[0]
    assert principal.user_id == DEMO_USER_ID
    assert job["payload"]["ci_trigger"]["head_sha"] == HEAD_SHA
    assert f"Commit={HEAD_SHA}" in job["payload"]["text"]
    assert "CI 成功本身不代表问题已修复" in job["payload"]["text"]
    assert job["payload"]["project_context"]["scope"]["commit_ref"] == HEAD_SHA
    snapshot = job["payload"]["project_context"]["memory_snapshot"]
    assert snapshot["scope"]["commit_ref"] == HEAD_SHA
    assert snapshot["memory_refs"] == []

    delivery = store.get_github_webhook_delivery("delivery-1")
    assert delivery["status"] == "enqueued"
    assert delivery["matched_count"] == 1
    assert delivery["enqueued_count"] == 1
    events = store.list_events(conversation["id"], workspace_id=DEMO_WORKSPACE_ID)
    assert any(event["type"] == "ci.revalidation.queued" for event in events)


def test_unapproved_workflow_is_recorded_but_never_enqueued(tmp_path, monkeypatch):
    client, store, _conversation, _link, scheduled = _fixture(tmp_path, monkeypatch)
    body = json.dumps(_workflow_payload(workflow_name="Docs CI")).encode("utf-8")
    response = client.post(
        "/integrations/github/webhook",
        content=body,
        headers=_signed_headers("delivery-docs", body),
    )

    assert response.status_code == 200
    assert response.json()["ignored"] == "workflow"
    assert scheduled == []
    assert store.get_github_webhook_delivery("delivery-docs")["status"] == "ignored_workflow"


def test_webhook_delivery_is_replay_safe(tmp_path, monkeypatch):
    client, store, _conversation, _link, scheduled = _fixture(tmp_path, monkeypatch)
    body = json.dumps(_workflow_payload(), separators=(",", ":")).encode("utf-8")
    headers = _signed_headers("delivery-repeat", body)

    first = client.post("/integrations/github/webhook", content=body, headers=headers)
    second = client.post("/integrations/github/webhook", content=body, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert len(scheduled) == 1
    assert store.get_github_webhook_delivery("delivery-repeat")["enqueued_count"] == 1


def test_received_delivery_recovers_existing_same_delivery_job_after_lease_expiry(tmp_path, monkeypatch):
    client, store, conversation, _link, scheduled = _fixture(tmp_path, monkeypatch)
    delivery_id = "delivery-crash-resume"
    assert store.begin_github_webhook_delivery(
        delivery_id=delivery_id,
        event="workflow_run",
        action="completed",
        repository="owner/game",
        head_sha=HEAD_SHA,
        workflow_run_id="991",
        workflow_name="Game CI",
        workflow_url="https://github.com/owner/game/actions/runs/991",
        conclusion="success",
    ) is True
    existing_job = store.enqueue_ci_revalidation(
        workspace_id=DEMO_WORKSPACE_ID,
        conversation_id=conversation["id"],
        trigger=_trigger(delivery_id),
    )
    before = _job_ids(store, conversation["id"])
    assert before[-1] == existing_job["id"]
    with store.engine.begin() as connection:
        connection.execute(
            update(store.github_webhook_deliveries)
            .where(store.github_webhook_deliveries.c.delivery_id == delivery_id)
            .values(claimed_at=time.time() - 61.0)
        )

    body = json.dumps(_workflow_payload(), separators=(",", ":")).encode("utf-8")
    response = client.post(
        "/integrations/github/webhook",
        content=body,
        headers=_signed_headers(delivery_id, body),
    )

    assert response.status_code == 200
    assert response.json()["recovered"] == 1
    assert response.json()["enqueued"] == 1
    assert _job_ids(store, conversation["id"]) == before
    assert len(scheduled) == 1
    assert scheduled[0][0]["id"] == existing_job["id"]
    delivery = store.get_github_webhook_delivery(delivery_id)
    assert delivery["status"] == "enqueued"
    assert delivery["enqueued_count"] == 1


def test_fresh_received_delivery_cannot_be_concurrently_reclaimed(tmp_path, monkeypatch):
    _client, store, _conversation, _link, _scheduled = _fixture(tmp_path, monkeypatch)
    delivery_id = "delivery-live-lease"
    kwargs = dict(
        delivery_id=delivery_id,
        event="workflow_run",
        action="completed",
        repository="owner/game",
        head_sha=HEAD_SHA,
        workflow_run_id="991",
        workflow_name="Game CI",
        workflow_url="https://github.com/owner/game/actions/runs/991",
        conclusion="success",
    )
    assert store.begin_github_webhook_delivery(**kwargs) is True
    assert store.begin_github_webhook_delivery(**kwargs) is False


def test_stale_index_binding_is_rechecked_against_current_link_metadata(tmp_path, monkeypatch):
    client, store, conversation, link, scheduled = _fixture(tmp_path, monkeypatch)
    current = store.get_external_issue_link(
        link["id"],
        conversation_id=conversation["id"],
        workspace_id=DEMO_WORKSPACE_ID,
    )
    meta = dict(current["meta"])
    context = dict(meta["github_context"])
    context["head_commit_sha"] = "d" * 40
    context.pop("selected_commit_sha", None)
    meta["github_context"] = context
    # Simulate a crash after authoritative linkage metadata changed but before its derived
    # github_code_bindings index was refreshed.
    with store.engine.begin() as connection:
        connection.execute(
            update(store.external_issue_links)
            .where(store.external_issue_links.c.id == link["id"])
            .values(meta=json.dumps(meta, ensure_ascii=False), updated_at=time.time())
        )

    body = json.dumps(_workflow_payload(), separators=(",", ":")).encode("utf-8")
    response = client.post(
        "/integrations/github/webhook",
        content=body,
        headers=_signed_headers("delivery-stale-binding", body),
    )

    assert response.status_code == 200
    assert response.json()["matched"] == 0
    assert response.json()["enqueued"] == 0
    assert response.json()["stale"] == 1
    assert scheduled == []
    delivery = store.get_github_webhook_delivery("delivery-stale-binding")
    assert delivery["status"] == "no_match"


def test_current_viewer_role_cannot_be_bypassed_by_ci_webhook(tmp_path, monkeypatch):
    client, store, _conversation, _link, scheduled = _fixture(tmp_path, monkeypatch)
    with store.engine.begin() as connection:
        connection.execute(
            update(store.memberships)
            .where(
                (store.memberships.c.workspace_id == DEMO_WORKSPACE_ID)
                & (store.memberships.c.user_id == DEMO_USER_ID)
            )
            .values(role="viewer")
        )
    body = json.dumps(_workflow_payload()).encode("utf-8")
    response = client.post(
        "/integrations/github/webhook",
        content=body,
        headers=_signed_headers("delivery-viewer", body),
    )

    assert response.status_code == 200
    assert response.json()["matched"] == 1
    assert response.json()["enqueued"] == 0
    assert response.json()["deferred"] == 1
    assert scheduled == []
    assert store.get_github_webhook_delivery("delivery-viewer")["status"] == "deferred"


def test_webhook_rejects_bad_signature_before_delivery_is_recorded(tmp_path, monkeypatch):
    client, store, _conversation, _link, scheduled = _fixture(tmp_path, monkeypatch)
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
    client, store, _conversation, _link, scheduled = _fixture(tmp_path, monkeypatch)
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
