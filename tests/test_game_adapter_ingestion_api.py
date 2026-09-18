from __future__ import annotations

import hashlib

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from worldforge.integrations.game_adapter import (
    GameAdapterCapabilities,
    RawAdapterEvidence,
    RawAdapterResult,
)
from worldforge.product import ConversationStore
from worldforge.product.game_adapter_ingestion_api import (
    _fetch_evidence_bytes,
    _normalize_loopback_endpoint,
    build_game_adapter_ingestion_router,
)
from worldforge.product.store import DEMO_USER_ID, DEMO_WORKSPACE_ID
from worldforge.security import Principal
from worldforge.storage import LocalObjectStorage


class FakeUnityAdapter:
    bodies = {
        "log": b"Boss phase 2 entered\n",
        "snapshot": b'{"active_scene":"BossArena","is_playing":true}',
        "screenshot": b"\x89PNG\r\n\x1a\nreal-unity-frame",
    }

    def __init__(self, endpoint, *, token=None, timeout_seconds=20.0):
        self.endpoint = endpoint
        self.token = token
        self.timeout_seconds = timeout_seconds
        self._capabilities = GameAdapterCapabilities(
            adapter_id="unity-test-adapter",
            engine="unity",
            engine_version="2022.3-test",
            supports_dry_run=True,
            supports_snapshot=True,
            supports_logs=True,
            supports_screenshots=True,
            mutating_actions=False,
        )

    async def capabilities(self):
        return self._capabilities

    async def execute(self, request):
        evidence = []
        for index, requested in enumerate(request.evidence_requests, start=1):
            kind = {"logs": "log", "snapshot": "snapshot", "screenshot": "screenshot"}[requested]
            body = self.bodies[kind]
            mime = {
                "log": "text/plain",
                "snapshot": "application/json",
                "screenshot": "image/png",
            }[kind]
            evidence.append(
                RawAdapterEvidence(
                    kind=kind,
                    locator=(
                        f"http://127.0.0.1:9030/v1/adapter/evidence/"
                        f"{index:032x}"
                    ),
                    sha256=hashlib.sha256(body).hexdigest(),
                    metadata={
                        "mime": mime,
                        "byte_size": len(body),
                        "width": 1280 if kind == "screenshot" else 0,
                        "height": 720 if kind == "screenshot" else 0,
                        "engine_object": (
                            "GameView/Camera.main" if kind == "screenshot"
                            else "UnityEditor"
                        ),
                        "scene": "BossArena",
                        "play_mode": "play",
                        "source_type": "verifier",
                        "verified": True,
                    },
                )
            )
        digest = hashlib.sha256(b"BossArena|play").hexdigest()
        return RawAdapterResult(
            adapter_id=self._capabilities.adapter_id,
            action_id=request.action_id,
            ticket_id=request.ticket.ticket_id,
            status="dry-run",
            before_snapshot_digest=digest,
            after_snapshot_digest=digest,
            evidence=tuple(evidence),
            metrics={"engine_read_only": True},
            message="read-only evidence",
        )


def _principal():
    return Principal(
        user_id=DEMO_USER_ID,
        workspace_id=DEMO_WORKSPACE_ID,
        email="demo@local.lingjing",
        role="owner",
    )


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDFORGE_ALLOW_LOCAL_GAME_ADAPTER", "1")
    monkeypatch.setenv(
        "LINGJING_GAME_ADAPTER_SIGNING_SECRET",
        "0123456789abcdef0123456789abcdef",
    )
    store = ConversationStore(
        db_path=tmp_path / "product.db",
        asset_dir=tmp_path / "assets",
    )
    storage = LocalObjectStorage(tmp_path / "objects")
    conversation = store.create_conversation(
        title="Boss engine evidence",
        scene="regression",
        workspace_id=DEMO_WORKSPACE_ID,
        created_by=DEMO_USER_ID,
    )

    bodies_by_path = {
        "/v1/adapter/evidence/" + f"{index:032x}": FakeUnityAdapter.bodies[kind]
        for index, kind in enumerate(("log", "snapshot", "screenshot"), start=1)
    }

    async def handler(request: httpx.Request):
        body = bodies_by_path.get(request.url.path)
        if body is None:
            return httpx.Response(404, json={"detail": "missing"})
        kind = (
            "screenshot" if body.startswith(b"\x89PNG")
            else "snapshot" if body.startswith(b"{")
            else "log"
        )
        mime = {
            "log": "text/plain",
            "snapshot": "application/json",
            "screenshot": "image/png",
        }[kind]
        return httpx.Response(
            200,
            content=body,
            headers={
                "content-type": mime,
                "x-lingjing-sha256": hashlib.sha256(body).hexdigest(),
            },
        )

    transport = httpx.MockTransport(handler)

    def http_client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return httpx.AsyncClient(*args, **kwargs)

    app = FastAPI()
    app.include_router(
        build_game_adapter_ingestion_router(
            store=store,
            storage=storage,
            require_principal=_principal,
            adapter_factory=FakeUnityAdapter,
            http_client_factory=http_client_factory,
        )
    )
    return store, storage, conversation, TestClient(app)


def test_loopback_endpoint_validation_rejects_ssrf_shapes():
    assert _normalize_loopback_endpoint("http://127.0.0.1:9030") == "http://127.0.0.1:9030"

    for value in (
        "https://127.0.0.1:9030",
        "http://localhost:9030",
        "http://10.0.0.2:9030",
        "http://127.0.0.1:80",
        "http://user:pass@127.0.0.1:9030",
        "http://127.0.0.1:9030/admin",
        "http://127.0.0.1:9030?token=x",
    ):
        with pytest.raises(ValueError):
            _normalize_loopback_endpoint(value)


def test_probe_and_capture_ingest_verified_bytes_without_persisting_token(tmp_path, monkeypatch):
    store, storage, conversation, client = _client(tmp_path, monkeypatch)
    token = "local-bearer-secret-never-persist"

    probe = client.post(
        f"/api/conversations/{conversation['id']}/game-adapter/probe",
        json={"endpoint": "http://127.0.0.1:9030", "token": token},
    )
    assert probe.status_code == 200
    assert probe.json()["connected"] is True
    assert probe.json()["capabilities"]["engine"] == "unity"
    assert probe.json()["capabilities"]["mutating_actions"] is False

    capture = client.post(
        f"/api/conversations/{conversation['id']}/game-adapter/capture",
        json={
            "endpoint": "http://127.0.0.1:9030",
            "token": token,
            "evidence_requests": ["logs", "snapshot", "screenshot"],
            "require_screenshot": True,
        },
    )
    assert capture.status_code == 200, capture.text
    payload = capture.json()
    assert payload["status"] == "ingested"
    assert payload["evidence_class"] == "external-engine-observation-unverified"
    assert payload["verifier_status"] == "not-run"
    assert payload["canonical_write_allowed"] is False
    assert len(payload["assets"]) == 3

    assets = store.list_assets(
        conversation["id"], workspace_id=DEMO_WORKSPACE_ID
    )
    assert {asset["meta"]["adapter_evidence_kind"] for asset in assets} == {
        "log",
        "snapshot",
        "screenshot",
    }
    for asset in assets:
        meta = asset["meta"]
        assert meta["source_type"] == "game-adapter"
        assert meta["evidence_class"] == "external-engine-observation-unverified"
        assert meta["adapter_id"] == "unity-test-adapter"
        assert "verified" not in meta
        assert token not in repr(meta)
        assert "127.0.0.1" not in repr(meta)
        assert hashlib.sha256(storage.get_bytes(asset["path"])).hexdigest() == meta["sha256"]

    audits = store.list_audit(workspace_id=DEMO_WORKSPACE_ID, limit=20)
    assert token not in repr(audits)
    assert "127.0.0.1" not in repr(audits)
    events = store.list_events(
        conversation["id"], workspace_id=DEMO_WORKSPACE_ID
    )
    assert any(event["type"] == "engine.evidence.ingested" for event in events)


@pytest.mark.asyncio
async def test_fetch_evidence_rejects_unsolicited_kind_before_network():
    called = False

    async def handler(request: httpx.Request):
        nonlocal called
        called = True
        return httpx.Response(200, content=b"should-not-fetch")

    transport = httpx.MockTransport(handler)

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return httpx.AsyncClient(*args, **kwargs)

    with pytest.raises(Exception, match="unsolicited"):
        await _fetch_evidence_bytes(
            endpoint="http://127.0.0.1:9030",
            token=None,
            evidence=(
                {
                    "kind": "video",
                    "locator": "http://127.0.0.1:9030/v1/adapter/evidence/" + "a" * 32,
                    "sha256": "b" * 64,
                    "meta": {"mime": "video/mp4"},
                },
            ),
            requested=("logs",),
            http_client_factory=factory,
        )
    assert called is False


def test_local_bridge_can_be_disabled_fail_closed(tmp_path, monkeypatch):
    _store, _storage, conversation, client = _client(tmp_path, monkeypatch)
    monkeypatch.setenv("WORLDFORGE_ALLOW_LOCAL_GAME_ADAPTER", "0")

    response = client.post(
        f"/api/conversations/{conversation['id']}/game-adapter/probe",
        json={"endpoint": "http://127.0.0.1:9030"},
    )
    assert response.status_code == 409
    assert "未启用本机 GameAdapter" in response.json()["detail"]
