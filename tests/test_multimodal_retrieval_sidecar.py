from __future__ import annotations

import httpx
import pytest

from worldforge.context.retrieval_sidecar import (
    MultimodalRetrievalClient,
    MultimodalRetrievalHit,
    MultimodalRetrievalResult,
    apply_retrieval_hits,
)
import worldforge.context.retrieval_sidecar as sidecar


class _Response:
    status_code = 200

    def json(self):
        return {
            "backend": "wemm-2b-256d",
            "latency_ms": 18.4,
            "hits": [
                {"asset_id": "a2", "score": 0.93, "modality": "image"},
                {"asset_id": "a1", "score": 0.88, "modality": "video", "start": 37.0},
                {"asset_id": "outside-task", "score": 1.0},
            ],
        }


class _Client:
    payload = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, _url, *, json):
        type(self).payload = json
        return _Response()


@pytest.mark.asyncio
async def test_sidecar_accepts_only_task_local_hits_and_sends_stable_metadata(monkeypatch, tmp_path):
    monkeypatch.setattr(sidecar.httpx, "AsyncClient", _Client)
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fixture")
    assets = [
        {
            "id": "a1",
            "name": "clip.mp4",
            "mime": "video/mp4",
            "path": str(video),
            "meta": {
                "kind": "video",
                "duration": 90.0,
                "_context": {"selected": True, "private_runtime_field": "no"},
            },
        },
        {
            "id": "a2",
            "name": "screen.png",
            "mime": "image/png",
            "path": str(tmp_path / "screen.png"),
            "meta": {"kind": "image"},
        },
    ]
    client = MultimodalRetrievalClient("http://retriever.internal", timeout_seconds=0.2)
    result = await client.rank("37 秒附近发生什么", assets)

    assert result.available is True
    assert result.backend == "wemm-2b-256d"
    assert [hit.asset_id for hit in result.hits] == ["a2", "a1"]
    assert result.hits[1].start == 37.0
    sent_meta = _Client.payload["assets"][0]["meta"]
    assert sent_meta["duration"] == 90.0
    assert "_context" not in sent_meta


@pytest.mark.asyncio
async def test_sidecar_failure_is_fail_open(monkeypatch):
    class _FailingClient(_Client):
        async def post(self, _url, *, json):
            raise httpx.ConnectError("offline")

    monkeypatch.setattr(sidecar.httpx, "AsyncClient", _FailingClient)
    client = MultimodalRetrievalClient("http://retriever.internal", timeout_seconds=0.1)
    result = await client.rank(
        "query",
        [{"id": "a1", "name": "x", "mime": "image/png", "path": "", "meta": {}}],
    )

    assert result.available is False
    assert result.hits == []
    assert result.error == "ConnectError"


def test_semantic_hits_promote_but_never_remove_deterministic_evidence():
    assets = [
        {
            "id": "baseline",
            "meta": {"_context": {"selected": True, "rank": 1, "reasons": ["modality-match"]}},
        },
        {"id": "semantic", "meta": {"_context": {"selected": False, "rank": None, "reasons": []}}},
    ]
    result = MultimodalRetrievalResult(
        hits=[MultimodalRetrievalHit("semantic", 0.97, start=42.0)],
        backend="wemm-2b-256d",
        latency_ms=12.0,
        available=True,
    )

    apply_retrieval_hits(assets, result)

    assert assets[0]["meta"]["_context"]["selected"] is True
    promoted = assets[1]["meta"]["_context"]
    assert promoted["selected"] is True
    assert promoted["semantic_score"] == 0.97
    assert promoted["time_hints"][0] == 42.0
    assert "semantic-retrieval" in promoted["reasons"]
