from fastapi.testclient import TestClient
from worldforge.api.app import app


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
        "auto", "demo", "deepseek", "qwen", "doubao",
        "openai", "anthropic", "gemini",
    } <= keys
    product = client.get("/api/product").json()
    assert product["name"] == "灵境游戏研发执行工作台"
    assert "视频" in product["accepted"]
    assert len(product["scenes"]) >= 5


def test_conversation_roundtrip():
    client = TestClient(app)
    conversation = client.post(
        "/api/conversations",
        json={"title": "测试任务", "scene": "battle_review"},
    ).json()
    response = client.get(
        f"/api/conversations/{conversation['id']}"
    )
    assert response.status_code == 200
    assert response.json()["title"] == "测试任务"
