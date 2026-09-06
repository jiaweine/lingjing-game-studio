from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from worldforge.api.app import app


def _register(client: TestClient, prefix: str) -> None:
    response = client.post(
        "/api/auth/register",
        json={
            "email": f"{prefix}-{uuid.uuid4().hex[:10]}@example.com",
            "password": "strong-password-123",
            "name": "Identity Reviewer",
            "workspace_name": f"Identity Lab {uuid.uuid4().hex[:6]}",
        },
    )
    assert response.status_code == 200, response.text


def _project_and_bound_conversation(client: TestClient) -> tuple[dict, dict]:
    project = client.post(
        "/api/projects",
        json={"name": "Atlas", "default_branch": "release"},
    )
    assert project.status_code == 200, project.text
    conversation = client.post(
        "/api/conversations",
        json={"title": "Identity review", "scene": "general"},
    )
    assert conversation.status_code == 200, conversation.text
    bound = client.post(
        f"/api/projects/{project.json()['id']}/conversations/{conversation.json()['id']}"
    )
    assert bound.status_code == 200, bound.text
    return project.json(), conversation.json()


def _stage_proposal(
    client: TestClient,
    project_id: str,
    conversation_id: str,
    content: str,
    *,
    build_ref: str | None = None,
    branch_ref: str | None = None,
) -> dict:
    sent = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={
            "content": content,
            "provider": "demo",
            "build_ref": build_ref,
            "branch_ref": branch_ref,
        },
    )
    assert sent.status_code == 200, sent.text
    proposals = client.get(
        f"/api/projects/{project_id}/memory-proposals",
        params={"status": "pending", "conversation_id": conversation_id},
    )
    assert proposals.status_code == 200, proposals.text
    rows = proposals.json()
    assert len(rows) == 1
    return rows[0]


def test_identity_suggestion_is_read_only_and_excludes_retracted_decoy():
    client = TestClient(app)
    _register(client, "identity-shadow")
    project, conversation = _project_and_bound_conversation(client)

    active = client.post(
        f"/api/projects/{project['id']}/memories",
        json={
            "memory_key": "combat.shield.cooldown",
            "kind": "fact",
            "content": "build 1.4.7 护盾冷却已确认是 6 秒。",
            "build_ref": "1.4.7",
            "branch_ref": "release",
        },
    )
    assert active.status_code == 200, active.text

    decoy = client.post(
        f"/api/projects/{project['id']}/memories",
        json={
            "memory_key": "legacy.shield.cooldown",
            "kind": "fact",
            "content": "已确认 build 1.4.7 护盾冷却是 5 秒。",
            "build_ref": "1.4.7",
            "branch_ref": "release",
        },
    )
    assert decoy.status_code == 200, decoy.text
    retracted = client.post(
        f"/api/projects/{project['id']}/memory-state",
        json={
            "memory_key": "legacy.shield.cooldown",
            "state": "retracted",
            "build_ref": "1.4.7",
            "branch_ref": "release",
            "note": "legacy identity 已废弃",
        },
    )
    assert retracted.status_code == 200, retracted.text

    proposal = _stage_proposal(
        client,
        project["id"],
        conversation["id"],
        "已确认 build 1.4.7 护盾冷却是 5 秒。",
        build_ref="1.4.7",
        branch_ref="release",
    )
    heads_before = client.get(
        f"/api/projects/{project['id']}/memory-heads"
    ).json()
    proposal_before = client.get(
        f"/api/projects/{project['id']}/memory-proposals",
        params={"status": "all", "conversation_id": conversation["id"]},
    ).json()

    response = client.get(
        f"/api/projects/{project['id']}/memory-proposals/{proposal['id']}/identity-suggestions"
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["read_only"] is True
    assert result["recommended_key"] == "combat.shield.cooldown"
    assert result["abstained"] is False
    assert result["best_score"] >= result["threshold"]
    assert "legacy.shield.cooldown" not in {
        row["memory_key"] for row in result["candidates"]
    }

    # The shadow GET must not create a revision or change review state.
    heads_after = client.get(
        f"/api/projects/{project['id']}/memory-heads"
    ).json()
    proposal_after = client.get(
        f"/api/projects/{project['id']}/memory-proposals",
        params={"status": "all", "conversation_id": conversation["id"]},
    ).json()
    assert heads_after == heads_before
    assert proposal_after == proposal_before
    assert proposal_after[0]["status"] == "pending"

    approved = client.post(
        f"/api/projects/{project['id']}/memory-proposals/{proposal['id']}/approve",
        json={"memory_key": "combat.shield.cooldown"},
    )
    assert approved.status_code == 200, approved.text
    no_longer_pending = client.get(
        f"/api/projects/{project['id']}/memory-proposals/{proposal['id']}/identity-suggestions"
    )
    assert no_longer_pending.status_code == 409


def test_identity_suggestion_abstains_on_different_named_entity():
    client = TestClient(app)
    _register(client, "identity-abstain")
    project, conversation = _project_and_bound_conversation(client)

    existing = client.post(
        f"/api/projects/{project['id']}/memories",
        json={
            "memory_key": "combat.fire_shield.cooldown",
            "kind": "fact",
            "content": "Confirmed fire_shield cooldown is 6 seconds.",
        },
    )
    assert existing.status_code == 200, existing.text

    proposal = _stage_proposal(
        client,
        project["id"],
        conversation["id"],
        "Confirmed ice_shield cooldown is 5 seconds.",
    )
    response = client.get(
        f"/api/projects/{project['id']}/memory-proposals/{proposal['id']}/identity-suggestions"
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["recommended_key"] is None
    assert result["abstained"] is True
    if result["candidates"]:
        assert result["candidates"][0]["score"] < result["threshold"]
