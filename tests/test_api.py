from fastapi.testclient import TestClient

from worldforge.api.app import app, product_store


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
