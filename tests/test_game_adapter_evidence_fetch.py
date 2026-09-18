from types import SimpleNamespace
import hashlib

import httpx
import pytest

from scripts import game_adapter_conformance as conformance


@pytest.mark.asyncio
async def test_fetch_evidence_probe_requires_same_adapter_origin_and_matching_sha(monkeypatch):
    body = b"real-unity-evidence-bytes"
    digest = hashlib.sha256(body).hexdigest()
    calls = []

    async def handler(request: httpx.Request):
        calls.append(
            {
                "url": str(request.url),
                "authorization": request.headers.get("authorization"),
            }
        )
        return httpx.Response(
            200,
            content=body,
            headers={
                "content-type": "image/png",
                "x-lingjing-sha256": digest,
            },
        )

    transport = httpx.MockTransport(handler)
    original_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return original_client(*args, **kwargs)

    monkeypatch.setattr(conformance.httpx, "AsyncClient", client_factory)
    args = SimpleNamespace(
        endpoint="http://127.0.0.1:9030",
        token="local-token",
        timeout=2.0,
        require_screenshot=True,
    )
    observation = SimpleNamespace(
        evidence=(
            {
                "kind": "screenshot",
                "locator": "http://127.0.0.1:9030/v1/adapter/evidence/evidence-1",
                "sha256": digest,
            },
        )
    )

    result = await conformance._fetch_evidence_probe(args, observation)

    assert result["passed"] is True
    assert result["screenshot_present"] is True
    assert result["items"][0]["bytes"] == len(body)
    assert result["items"][0]["sha256"] == digest
    assert calls == [
        {
            "url": "http://127.0.0.1:9030/v1/adapter/evidence/evidence-1",
            "authorization": "Bearer local-token",
        }
    ]


@pytest.mark.asyncio
async def test_fetch_evidence_probe_rejects_locator_outside_configured_adapter(monkeypatch):
    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(
        lambda request: (_ for _ in ()).throw(AssertionError("foreign locator must not be fetched"))
    )

    def client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return original_client(*args, **kwargs)

    monkeypatch.setattr(conformance.httpx, "AsyncClient", client_factory)
    args = SimpleNamespace(
        endpoint="http://127.0.0.1:9030",
        token=None,
        timeout=2.0,
        require_screenshot=False,
    )
    observation = SimpleNamespace(
        evidence=(
            {
                "kind": "log",
                "locator": "http://127.0.0.1:9999/v1/adapter/evidence/stolen",
                "sha256": "a" * 64,
            },
        )
    )

    result = await conformance._fetch_evidence_probe(args, observation)

    assert result["passed"] is False
    assert result["items"][0]["error"] == "locator-outside-adapter-evidence-prefix"
