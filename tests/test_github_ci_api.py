import hashlib
import hmac
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from worldforge.product import ConversationStore
from worldforge.product.github_ci_api import build_github_ci_router
from worldforge.product.store import DEMO_USER_ID, DEMO_WORKSPACE_ID
from worldforge.security import Principal


def _principal():
    return Principal(
        user_id=DEMO_USER_ID,
        workspace_id=DEMO_WORKSPACE_ID,
        email="demo@local.lingjing",
        role="owner",
    )


def _signature(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _fixture(tmp_path):
    store = ConversationStore(
        db_path=tmp_path / "product.db",
        asset_dir=tmp_path / "assets",
    )
    conversation = store.create_conversation(
        title="Boss CI 修复验证",
        scene="regression",
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
        commit={
            "sha": "a" * 40,
            "url": f"https://github.com/owner/game/commit/{'a' * 40}",
            "message": "Fix boss phase",
        },
    )
    source_payload = {
        "text": "复现 Boss phase 2",
        "provider": "auto",
        "history_snapshot": {"version": 1, "count": 0, "messages": []},
        "asset_ids": [],
        "actor_id": DEMO_USER_ID,
        "project_context": {
            "actor_id": DEMO_USER_ID,
            "scope": {"commit_ref": "old-commit", "environment_ref": "staging"},
        },
    }
    _, source_job = store.create_message_job(
        workspace_id=DEMO_WORKSPACE_ID,
        conversation_id=conversation["id"],
        content="复现 Boss phase 2",
        asset_ids=[],
        job_payload=source_payload,
    )
    store.claim_job("test-worker", job_id=source_job["id"])
    store.complete_job_answer(
        source_job["id"],
        workspace_id=DEMO_WORKSPACE_ID,
        content="已有复现结果",
        payload={"outcome": {"issue_lifecycle": True, "verified": False}},
    )
    store.set_github_ci_subscription(
        link["id"],
        conversation_id=conversation["id"],
        workspace_id=DEMO_WORKSPACE_ID,
        enabled=True,
        updated_by=DEMO_USER_ID,
        workflow_name="Build Game",
    )

    scheduled = []

    async def schedule_job(job, background_tasks, principal):
        scheduled.append((job["id"], principal.user_id))

    app = FastAPI()
    app.include_router(
        build_github_ci_router(
            store=store,
            require_principal=_principal,
            schedule_job=schedule_job,
        )
    )
    return store, conversation, link, scheduled, TestClient(app)


def _workflow_body(*, workflow_name="Build Game", delivery_sha=None):
    sha = delivery_sha or ("a" * 40)
    return json.dumps(
        {
            "action": "completed",
            "repository": {"full_name": "owner/game"},
            "workflow_run": {
                "id": 9001,
                "name": workflow_name,
                "head_sha": sha,
                "status": "completed",
                "conclusion": "success",
            },
        },
        separators=(",", ":"),
    ).encode()


def test_signed_successful_workflow_enqueues_one_revalidation_and_dedupes(tmp_path, monkeypatch):
    store, conversation, _link, scheduled, client = _fixture(tmp_path)
    secret = "webhook-secret-for-tests"
    monkeypatch.setenv("WORLDFORGE_GITHUB_WEBHOOK_SECRET", secret)
    body = _workflow_body()
    headers = {
        "X-GitHub-Event": "workflow_run",
        "X-GitHub-Delivery": "delivery-1",
        "X-Hub-Signature-256": _signature(secret, body),
        "Content-Type": "application/json",
    }

    first = client.post("/integrations/github/webhook", content=body, headers=headers)
    assert first.status_code == 200
    assert first.json()["matched_routes"] == 1
    assert first.json()["enqueued_jobs"] == 1
    assert len(scheduled) == 1

    latest = store.latest_job(conversation["id"], workspace_id=DEMO_WORKSPACE_ID)
    assert latest["payload"]["automation"]["source"] == "github_ci"
    assert latest["payload"]["automation"]["workflow_name"] == "Build Game"
    assert latest["payload"]["project_context"]["scope"]["commit_ref"] == "a" * 40
    assert latest["payload"]["project_context"]["scope"]["environment_ref"] == "staging"

    duplicate = client.post("/integrations/github/webhook", content=body, headers=headers)
    assert duplicate.status_code == 200
    assert duplicate.json()["duplicate"] is True
    assert len(scheduled) == 1
    assert store.latest_job(conversation["id"], workspace_id=DEMO_WORKSPACE_ID)["id"] == latest["id"]


def test_wrong_workflow_and_bad_signature_do_not_enqueue(tmp_path, monkeypatch):
    store, conversation, _link, scheduled, client = _fixture(tmp_path)
    secret = "webhook-secret-for-tests"
    monkeypatch.setenv("WORLDFORGE_GITHUB_WEBHOOK_SECRET", secret)

    wrong = _workflow_body(workflow_name="Unit Tests")
    wrong_response = client.post(
        "/integrations/github/webhook",
        content=wrong,
        headers={
            "X-GitHub-Event": "workflow_run",
            "X-GitHub-Delivery": "delivery-wrong-workflow",
            "X-Hub-Signature-256": _signature(secret, wrong),
            "Content-Type": "application/json",
        },
    )
    assert wrong_response.status_code == 200
    assert wrong_response.json()["matched_routes"] == 0
    assert scheduled == []

    before = store.latest_job(conversation["id"], workspace_id=DEMO_WORKSPACE_ID)["id"]
    bad = _workflow_body()
    bad_response = client.post(
        "/integrations/github/webhook",
        content=bad,
        headers={
            "X-GitHub-Event": "workflow_run",
            "X-GitHub-Delivery": "delivery-bad-signature",
            "X-Hub-Signature-256": "sha256=" + ("0" * 64),
            "Content-Type": "application/json",
        },
    )
    assert bad_response.status_code == 401
    assert scheduled == []
    assert store.latest_job(conversation["id"], workspace_id=DEMO_WORKSPACE_ID)["id"] == before
    try:
        store.get_github_ci_delivery("delivery-bad-signature")
        assert False, "bad signatures must not create delivery records"
    except KeyError:
        pass
