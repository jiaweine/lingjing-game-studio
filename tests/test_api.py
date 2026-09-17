from fastapi.testclient import TestClient

from worldforge.api.app import app, product_store
from worldforge.product.control import github_issue_publisher
from worldforge.product.github_issue_publisher import PublishedIssueComment


def test_health_and_runtime():
    client = TestClient(app)
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["product"] == "灵境游戏工作台"
    data = client.get("/api/runtime").json()
    assert data["decision_model"]["counterfactual"] is True
    assert len(data["plugins"]) >= 5
    assert data["policy"]["external_api"] is False


def test_scenarios():
    client = TestClient(app)
    response = client.get("/api/scenarios")
    assert response.status_code == 200
    assert len(response.json()) >= 4


def test_provider_gateway_and_product_info():
    client = TestClient(app)
    providers = client.get("/api/providers").json()
    keys = {item["key"] for item in providers}
    assert {
        "auto",
        "demo",
        "local_omni",
        "deepseek",
        "qwen",
        "doubao",
        "openai",
        "anthropic",
        "gemini",
    } <= keys
    product = client.get("/api/product").json()
    assert product["name"] == "灵境游戏研发执行工作台"
    assert "视频" in product["accepted"]
    assert "音频" in product["accepted"]
    assert len(product["scenes"]) >= 5


def test_conversation_roundtrip():
    client = TestClient(app)
    conversation = client.post(
        "/api/conversations",
        json={"title": "测试任务", "scene": "battle_review"},
    ).json()
    response = client.get(f"/api/conversations/{conversation['id']}")
    assert response.status_code == 200
    assert response.json()["title"] == "测试任务"


def test_external_github_issue_linkage_roundtrip():
    client = TestClient(app)
    conversation = client.post(
        "/api/conversations",
        json={"title": "外部 Issue 关联", "scene": "battle_review"},
    ).json()
    conversation_id = conversation["id"]

    created = client.post(
        f"/api/conversations/{conversation_id}/external-links",
        json={
            "repository": "jiaweine/lingjing-game-studio",
            "issue_number": 29,
            "title": "Push verified results back into GitHub/Jira/CI workflows",
        },
    )
    assert created.status_code == 200
    link = created.json()
    assert link["provider"] == "github"
    assert link["external_key"] == "29"
    assert link["external_url"].endswith("/jiaweine/lingjing-game-studio/issues/29")

    listed = client.get(
        f"/api/conversations/{conversation_id}/external-links"
    )
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [link["id"]]

    control = client.get(f"/api/conversations/{conversation_id}/control")
    assert control.status_code == 200
    assert control.json()["external_links"][0]["id"] == link["id"]

    removed = client.delete(
        f"/api/conversations/{conversation_id}/external-links/{link['id']}"
    )
    assert removed.status_code == 200
    assert removed.json() == {"ok": True}
    assert client.get(
        f"/api/conversations/{conversation_id}/external-links"
    ).json() == []


def _create_push_fixture(*, verified: bool):
    client = TestClient(app)
    conversation = client.post(
        "/api/conversations",
        json={"title": "GitHub Push 测试", "scene": "battle_review"},
    ).json()
    conversation_id = conversation["id"]
    product_store.add_message(
        conversation_id,
        "user",
        "验证问题。\n\n【验证范围】Build=1.4.7 | Commit=abc123",
        {},
        workspace_id=conversation["workspace_id"],
    )
    product_store.add_message(
        conversation_id,
        "assistant",
        "用户可见的问题验证结果。",
        {
            "outcome": {
                "issue_lifecycle": True,
                "requires_project_verification": True,
                "state": "verified" if verified else "needs_verifier_decision",
                "label": "已验证" if verified else "需要验证结论",
                "verified": verified,
                "reason": "独立 Verifier 已确认" if verified else "仍需独立 Verifier",
            },
            "evidence": [{"title": "回归日志", "locator": "/private/not-for-github.log"}],
        },
        workspace_id=conversation["workspace_id"],
    )
    link = client.post(
        f"/api/conversations/{conversation_id}/external-links",
        json={"repository": "owner/game", "issue_number": 7},
    ).json()
    return client, conversation, link


def test_github_issue_push_requires_server_side_credential():
    client, conversation, link = _create_push_fixture(verified=False)
    response = client.post(
        f"/api/conversations/{conversation['id']}/external-links/{link['id']}/push",
        json={"kind": "reproduction"},
    )
    assert response.status_code == 503
    assert "WORLDFORGE_GITHUB_TOKEN" in response.json()["detail"]


def test_github_verification_push_is_blocked_before_verifier():
    client, conversation, link = _create_push_fixture(verified=False)
    response = client.post(
        f"/api/conversations/{conversation['id']}/external-links/{link['id']}/push",
        json={"kind": "verification"},
    )
    assert response.status_code == 409
    assert "Verifier" in response.json()["detail"]


def test_github_issue_push_posts_then_updates_same_comment(monkeypatch):
    client, conversation, link = _create_push_fixture(verified=True)
    calls = []

    async def fake_publish_comment(**kwargs):
        calls.append(kwargs)
        return PublishedIssueComment(
            comment_id=4321,
            html_url="https://github.com/owner/game/issues/7#issuecomment-4321",
            updated=kwargs.get("existing_comment_id") is not None,
        )

    monkeypatch.setattr(github_issue_publisher, "publish_comment", fake_publish_comment)
    endpoint = (
        f"/api/conversations/{conversation['id']}/external-links/{link['id']}/push"
    )
    first = client.post(endpoint, json={"kind": "verification"})
    second = client.post(endpoint, json={"kind": "verification"})

    assert first.status_code == 200
    assert first.json()["updated"] is False
    assert second.status_code == 200
    assert second.json()["updated"] is True
    assert calls[0]["existing_comment_id"] is None
    assert calls[1]["existing_comment_id"] == 4321
    assert "/private/not-for-github.log" not in calls[0]["body"]
    persisted = client.get(
        f"/api/conversations/{conversation['id']}/external-links"
    ).json()[0]
    assert persisted["sync_state"] == "verification_pushed"
    assert persisted["meta"]["github_comments"]["verification"]["id"] == 4321


def test_product_job_can_be_cancelled():
    client = TestClient(app)
    conversation = client.post(
        "/api/conversations",
        json={"title": "停止测试", "scene": "battle_review"},
    ).json()
    job = product_store.enqueue_job(
        workspace_id=conversation["workspace_id"],
        conversation_id=conversation["id"],
        payload={"text": "test", "provider": "auto", "history": [], "asset_ids": []},
    )
    response = client.post(f"/api/jobs/{job['id']}/cancel")
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert client.post(f"/api/jobs/{job['id']}/cancel").json()["status"] == "cancelled"


def test_spoofed_image_upload_is_rejected():
    client = TestClient(app)
    response = client.post(
        "/api/assets",
        files={"file": ("fake.png", b"<html>not-an-image</html>", "image/png")},
    )
    assert response.status_code == 415
