from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from worldforge.api.app import app


def _email(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}@example.com"


def test_memory_heads_lists_all_scopes_without_inference_shadowing():
    client = TestClient(app)
    registration = client.post(
        "/api/auth/register",
        json={
            "email": _email("governance-owner"),
            "password": "strong-password-123",
            "name": "Governance Owner",
            "workspace_name": f"Governance Lab {uuid.uuid4().hex[:6]}",
        },
    )
    assert registration.status_code == 200

    project = client.post(
        "/api/projects", json={"name": "Atlas", "default_branch": "release"}
    ).json()

    rows = [
        {
            "memory_key": "combat.shield.cooldown",
            "kind": "fact",
            "content": "默认护盾冷却是 6 秒",
        },
        {
            "memory_key": "combat.shield.cooldown",
            "kind": "fact",
            "content": "build 1.4.7 护盾冷却是 5 秒",
            "build_ref": "1.4.7",
            "branch_ref": "release",
        },
        {
            "memory_key": "combat.shield.cooldown",
            "kind": "fact",
            "content": "build 2.0.0 护盾冷却是 4 秒",
            "build_ref": "2.0.0",
            "branch_ref": "release",
        },
    ]
    created = []
    for payload in rows:
        response = client.post(
            f"/api/projects/{project['id']}/memories", json=payload
        )
        assert response.status_code == 200
        created.append(response.json())

    retracted = client.post(
        f"/api/projects/{project['id']}/memory-state",
        json={
            "memory_key": "combat.shield.cooldown",
            "state": "retracted",
            "build_ref": "2.0.0",
            "branch_ref": "release",
            "note": "2.0.0 规则已废弃",
        },
    )
    assert retracted.status_code == 200
    assert retracted.json()["revision"] == 2

    # Inference view without a supplied scope deliberately resolves only general memory.
    inference = client.get(f"/api/projects/{project['id']}/memories")
    assert inference.status_code == 200
    assert [(row["build_ref"], row["state"]) for row in inference.json()] == [
        (None, "active")
    ]

    # Governance view must expose every current key×scope head, including tombstones.
    heads = client.get(f"/api/projects/{project['id']}/memory-heads")
    assert heads.status_code == 200
    by_build = {row["build_ref"]: row for row in heads.json()}
    assert set(by_build) == {None, "1.4.7", "2.0.0"}
    assert by_build[None]["content"] == "默认护盾冷却是 6 秒"
    assert by_build["1.4.7"]["state"] == "active"
    assert by_build["1.4.7"]["revision"] == 1
    assert by_build["2.0.0"]["state"] == "retracted"
    assert by_build["2.0.0"]["revision"] == 2

    active_only = client.get(
        f"/api/projects/{project['id']}/memory-heads?include_nonactive=false"
    )
    assert active_only.status_code == 200
    assert {row["build_ref"] for row in active_only.json()} == {None, "1.4.7"}
