from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from worldforge.api.app import app


def _email(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}@example.com"


def test_project_memory_is_frozen_as_refs_and_reaches_analyzer_context():
    client = TestClient(app)
    registration = client.post(
        "/api/auth/register",
        json={
            "email": _email("memory-owner"),
            "password": "strong-password-123",
            "name": "Memory Owner",
            "workspace_name": f"Memory Lab {uuid.uuid4().hex[:6]}",
        },
    )
    assert registration.status_code == 200

    project_response = client.post(
        "/api/projects",
        json={"name": "Atlas", "default_branch": "main"},
    )
    assert project_response.status_code == 200
    project = project_response.json()

    conversation = client.post(
        "/api/conversations",
        json={"title": "Shield follow-up", "scene": "regression"},
    ).json()
    binding = client.post(
        f"/api/projects/{project['id']}/conversations/{conversation['id']}"
    )
    assert binding.status_code == 200

    memory_response = client.post(
        f"/api/projects/{project['id']}/memories",
        json={
            "memory_key": "combat.shield.cooldown",
            "kind": "fact",
            "content": "build 1.4.7 的护盾冷却已确认是 4 秒",
            "build_ref": "1.4.7",
            "confidence": 1.0,
            "importance": 0.9,
            "source_type": "user_api",
        },
    )
    assert memory_response.status_code == 200
    memory = memory_response.json()

    sent = client.post(
        f"/api/conversations/{conversation['id']}/messages",
        json={
            "content": "护盾冷却现在是多少？",
            "asset_ids": [],
            "provider": "auto",
            "build_ref": "1.4.7",
        },
    )
    assert sent.status_code == 200
    job_id = sent.json()["job_id"]

    job = client.get(f"/api/jobs/{job_id}")
    assert job.status_code == 200
    payload = job.json()["payload"]
    project_context = payload["project_context"]
    assert project_context["project_id"] == project["id"]
    assert project_context["scope"]["build_ref"] == "1.4.7"
    refs = project_context["memory_snapshot"]["memory_refs"]
    assert refs == [
        {
            "id": memory["id"],
            "revision": memory["revision"],
            "retrieval_score": refs[0]["retrieval_score"],
        }
    ]
    # Governed long-term memory text must not be duplicated into queued job payloads.
    assert "build 1.4.7 的护盾冷却已确认是 4 秒" not in repr(project_context)

    state = client.get(f"/api/conversations/{conversation['id']}")
    assert state.status_code == 200
    assistants = [row for row in state.json()["messages"] if row["role"] == "assistant"]
    assert assistants
    result = assistants[-1]["payload"]
    context = result["context"]
    assert context["project_id"] == project["id"]
    assert context["project_memory_selected"] == 1
    assert context["project_memory_ids"] == [memory["id"]]
    assert context["project_memory_scope"]["build_ref"] == "1.4.7"
    assert context["project_memory_invalidated_refs"] == 0


def test_unbound_conversation_does_not_guess_project_memory():
    client = TestClient(app)
    registration = client.post(
        "/api/auth/register",
        json={
            "email": _email("unbound-owner"),
            "password": "strong-password-456",
            "name": "Unbound Owner",
            "workspace_name": f"Unbound Lab {uuid.uuid4().hex[:6]}",
        },
    )
    assert registration.status_code == 200

    project = client.post("/api/projects", json={"name": "Project One"}).json()
    memory = client.post(
        f"/api/projects/{project['id']}/memories",
        json={
            "memory_key": "release.rule",
            "kind": "constraint",
            "content": "发布前必须运行回归套件",
        },
    )
    assert memory.status_code == 200

    conversation = client.post(
        "/api/conversations",
        json={"title": "Other task", "scene": "general"},
    ).json()
    sent = client.post(
        f"/api/conversations/{conversation['id']}/messages",
        json={"content": "发布前有什么要求？", "provider": "auto"},
    )
    assert sent.status_code == 200
    job = client.get(f"/api/jobs/{sent.json()['job_id']}").json()
    assert job["payload"]["project_context"] is None

    state = client.get(f"/api/conversations/{conversation['id']}").json()
    assistant = [row for row in state["messages"] if row["role"] == "assistant"][-1]
    assert assistant["payload"]["context"]["project_memory_selected"] == 0
    assert assistant["payload"]["context"]["project_id"] is None
