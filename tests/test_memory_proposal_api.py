from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from worldforge.api.app import app


def _register(client: TestClient):
    response = client.post(
        "/api/auth/register",
        json={
            "email": f"proposal-{uuid.uuid4().hex[:12]}@example.com",
            "password": "strong-password-123",
            "name": "Memory Reviewer",
            "workspace_name": f"Memory Proposal Lab {uuid.uuid4().hex[:6]}",
        },
    )
    assert response.status_code == 200, response.text


def _conversation(client: TestClient, title: str) -> dict:
    response = client.post(
        "/api/conversations",
        json={"title": title, "scene": "general"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _bind(client: TestClient, project_id: str, conversation_id: str):
    response = client.post(
        f"/api/projects/{project_id}/conversations/{conversation_id}"
    )
    assert response.status_code == 200, response.text


def test_pending_proposal_requires_approval_then_recalls_across_conversations():
    client = TestClient(app)
    _register(client)

    project_response = client.post(
        "/api/projects",
        json={"name": "Atlas", "default_branch": "main"},
    )
    assert project_response.status_code == 200, project_response.text
    project = project_response.json()

    source_conversation = _conversation(client, "Release policy source")
    _bind(client, project["id"], source_conversation["id"])

    first = client.post(
        f"/api/conversations/{source_conversation['id']}/messages",
        json={
            "content": "发布前必须运行全套回归。",
            "provider": "demo",
        },
    )
    assert first.status_code == 200, first.text
    source_message_id = first.json()["message"]["id"]

    pending_response = client.get(
        f"/api/projects/{project['id']}/memory-proposals",
        params={"status": "pending", "conversation_id": source_conversation["id"]},
    )
    assert pending_response.status_code == 200, pending_response.text
    pending = pending_response.json()
    assert len(pending) == 1
    proposal = pending[0]
    assert proposal["message_id"] == source_message_id
    assert proposal["kind"] == "constraint"
    assert proposal["content"] == "发布前必须运行全套回归。"

    # Pending proposals are not truth and therefore must not appear in project memory yet.
    memories_before = client.get(
        f"/api/projects/{project['id']}/memories"
    ).json()
    assert memories_before == []

    approved_response = client.post(
        f"/api/projects/{project['id']}/memory-proposals/{proposal['id']}/approve",
        json={
            "memory_key": "release.regression.required",
            "note": "确认是跨任务长期约束",
        },
    )
    assert approved_response.status_code == 200, approved_response.text
    approved = approved_response.json()
    memory = approved["memory"]
    assert approved["proposal"]["status"] == "approved"
    assert memory["source_type"] == "user_confirmed"
    assert memory["source_id"] == f"proposal:{proposal['id']}"
    assert memory["revision"] == 1

    # Approval is idempotent: retries/repeated clicks cannot create another revision.
    repeated = client.post(
        f"/api/projects/{project['id']}/memory-proposals/{proposal['id']}/approve",
        json={"memory_key": "must.not.replace.on.replay"},
    )
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["memory"]["id"] == memory["id"]

    followup_conversation = _conversation(client, "Release policy follow-up")
    _bind(client, project["id"], followup_conversation["id"])
    followup = client.post(
        f"/api/conversations/{followup_conversation['id']}/messages",
        json={
            "content": "发布前有什么要求？",
            "provider": "demo",
        },
    )
    assert followup.status_code == 200, followup.text

    job = client.get(f"/api/jobs/{followup.json()['job_id']}")
    assert job.status_code == 200, job.text
    payload = job.json()["payload"]
    refs = payload["project_context"]["memory_snapshot"]["memory_refs"]
    assert memory["id"] in {row["id"] for row in refs}
    # Queue payloads only contain locators; governed memory text stays in the authoritative DB.
    assert "发布前必须运行全套回归" not in repr(payload["project_context"])

    state = client.get(
        f"/api/conversations/{followup_conversation['id']}"
    )
    assert state.status_code == 200
    assistants = [
        row for row in state.json()["messages"] if row["role"] == "assistant"
    ]
    assert assistants
    context = assistants[-1]["payload"]["context"]
    assert context["project_id"] == project["id"]
    assert memory["id"] in context["project_memory_ids"]
    assert context["project_memory_selected"] >= 1


def test_unbound_and_uncertain_user_text_do_not_create_proposals():
    client = TestClient(app)
    _register(client)
    project = client.post("/api/projects", json={"name": "Atlas"}).json()

    unbound = _conversation(client, "Unbound")
    sent = client.post(
        f"/api/conversations/{unbound['id']}/messages",
        json={"content": "发布前必须运行回归。", "provider": "demo"},
    )
    assert sent.status_code == 200
    assert client.get(
        f"/api/projects/{project['id']}/memory-proposals",
        params={"status": "all"},
    ).json() == []

    bound = _conversation(client, "Bound uncertain")
    _bind(client, project["id"], bound["id"])
    uncertain = client.post(
        f"/api/conversations/{bound['id']}/messages",
        json={
            "content": "这个问题可能是冷却导致。是不是要改成 4 秒？",
            "provider": "demo",
        },
    )
    assert uncertain.status_code == 200
    proposals = client.get(
        f"/api/projects/{project['id']}/memory-proposals",
        params={"status": "all", "conversation_id": bound["id"]},
    )
    assert proposals.status_code == 200
    assert proposals.json() == []
