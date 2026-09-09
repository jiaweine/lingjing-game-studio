from __future__ import annotations

import json
import shutil
import subprocess
import sys
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from worldforge.api.app import app

OUT = ROOT / "outputs"
ASSETS = OUT / "demo_assets"
ASSETS.mkdir(parents=True, exist_ok=True)


def build_assets():
    frame = Image.new("RGB", (960, 540), "#10151d")
    draw = ImageDraw.Draw(frame)
    draw.rectangle((70, 70, 890, 470), outline="#8b7cff", width=8)
    draw.ellipse((410, 190, 550, 330), fill="#8b7cff")
    frame.save(ASSETS / "boss_frame.png")
    (ASSETS / "battle.log").write_text(
        "59.8 damage=120\n60.0 shield=0\n60.2 hp=18\n",
        encoding="utf-8",
    )

    video = ASSETS / "boss_replay.mp4"
    if shutil.which("ffmpeg"):
        subprocess.run([
            "ffmpeg", "-y", "-loop", "1",
            "-i", str(ASSETS / "boss_frame.png"),
            "-t", "1.2", "-pix_fmt", "yuv420p",
            "-vf", "scale=960:540",
            str(video),
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return video if video.exists() else None


report = {"checks": {}, "conversation_id": None, "events": [], "errors": []}
video = build_assets()

with TestClient(app) as client:
    health = client.get("/api/health")
    assert health.status_code == 200
    report["checks"]["backend_health"] = True

    providers = client.get("/api/providers").json()
    assert len(providers) >= 7
    report["checks"]["provider_gateway"] = True

    conversation = client.post(
        "/api/conversations",
        json={"title": "Boss 战问题复现", "scene": "battle_review"},
    ).json()
    conversation_id = conversation["id"]
    report["conversation_id"] = conversation_id

    specs = [
        ("boss_frame.png", "image/png"),
        ("battle.log", "text/plain"),
    ]
    if video:
        specs.insert(1, ("boss_replay.mp4", "video/mp4"))

    asset_ids = []
    for filename, mime in specs:
        with (ASSETS / filename).open("rb") as handle:
            response = client.post(
                "/api/assets",
                files={"file": (filename, handle, mime)},
                data={"conversation_id": conversation_id},
            )
        assert response.status_code == 200, response.text
        asset_ids.append(response.json()["id"])
    report["checks"]["multimodal_ingest"] = True

    response = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={
            "content": "复现 60 秒后的异常掉血，找稳定触发条件并给出回归顺序。",
            "asset_ids": asset_ids,
            "provider": "demo",
        },
    )
    assert response.status_code == 200, response.text

    data = client.get(f"/api/conversations/{conversation_id}").json()
    assert len(data["messages"]) == 2
    assert data["messages"][-1]["role"] == "assistant"
    result = data["messages"][-1]["payload"]
    evidence = list(result.get("evidence") or [])
    assert evidence, "evidence controller must preserve at least one auditable evidence node"
    context = dict(result.get("context") or {})
    assert context.get("task_assets") == len(asset_ids)
    graph = dict(result.get("claim_evidence_graph") or {})
    assert graph.get("claims"), graph
    assert graph.get("evidence"), graph
    assert graph.get("requirements", {}).get(
        "synthetic_runtime_counts_as_project_verification"
    ) is False
    # Evidence selection is relevance/budget driven; it must not mechanically emit one
    # evidence row per uploaded asset. All task assets remain preserved in context instead.
    report["checks"]["execution_result"] = True
    report["events"] = [event["type"] for event in data["events"]]
    assert report["events"][-1] == "answer.ready"
    report["checks"]["durable_events"] = True

    with client.websocket_connect(f"/ws/conversations/{conversation_id}") as websocket:
        seen = []
        for _ in range(24):
            event = websocket.receive_json()
            if event.get("type") == "heartbeat":
                continue
            seen.append(event.get("type"))
            if event.get("type") == "answer.ready":
                break
        assert "progress" in seen and "answer.ready" in seen
    report["checks"]["websocket_replay"] = True

    response = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={
            "content": "继续核对减伤覆盖和技能冷却是不是撞在同一个时间窗。",
            "asset_ids": [],
            "provider": "demo",
        },
    )
    assert response.status_code == 200, response.text
    data = client.get(f"/api/conversations/{conversation_id}").json()
    assert len(data["messages"]) == 4
    assert data["messages"][-1]["payload"].get("context", {}).get("history_messages") == 2
    assert data["messages"][-1]["payload"].get("context", {}).get("task_assets") == len(asset_ids)
    report["checks"]["followup_context"] = True

report["ok"] = all(report["checks"].values()) and not report["errors"]
OUT.mkdir(exist_ok=True)
(OUT / "product_backend_e2e.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2),
    encoding="utf-8",
)
print(json.dumps(report, ensure_ascii=False, indent=2))
if not report["ok"]:
    raise SystemExit(1)
