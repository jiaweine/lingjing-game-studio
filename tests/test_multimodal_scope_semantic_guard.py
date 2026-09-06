from __future__ import annotations

import pytest

from worldforge.context.scoped_evidence import ScopedEvidenceController
from worldforge.context.scoped_retrieval import ScopedMultimodalRetrievalClient
import worldforge.context.retrieval_sidecar as sidecar


class _Response:
    status_code = 200

    def json(self):
        # Deliberately try to smuggle the wrong-build asset back with an extreme score.
        return {
            "backend": "hostile-fixture",
            "latency_ms": 1.0,
            "hits": [
                {"asset_id": "wrong-build", "score": 100.0, "modality": "video"},
                {"asset_id": "current-build", "score": 0.8, "modality": "text"},
            ],
        }


class _Client:
    payload = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def post(self, _url, *, json):
        type(self).payload = json
        return _Response()


def _asset(asset_id: str, kind: str, *, eligible: bool):
    mime = {
        "text": "text/plain",
        "image": "image/png",
        "video": "video/mp4",
        "audio": "audio/wav",
    }[kind]
    return {
        "id": asset_id,
        "name": asset_id,
        "mime": mime,
        "path": "",
        "meta": {
            "kind": kind,
            "_context": {
                "kind": kind,
                "selected": eligible,
                "scope_eligible": eligible,
                "reasons": [],
            },
        },
    }


@pytest.mark.asyncio
async def test_scoped_retriever_never_sends_or_accepts_wrong_build_asset(monkeypatch):
    monkeypatch.setattr(sidecar.httpx, "AsyncClient", _Client)
    assets = [
        _asset("current-build", "text", eligible=True),
        _asset("wrong-build", "video", eligible=False),
    ]
    client = ScopedMultimodalRetrievalClient(
        "http://retriever.internal", timeout_seconds=0.1
    )

    result = await client.rank("为什么当前 build 出错", assets)

    assert [row["id"] for row in _Client.payload["assets"]] == ["current-build"]
    assert [hit.asset_id for hit in result.hits] == ["current-build"]
    assert all(hit.asset_id != "wrong-build" for hit in result.hits)


def test_scoped_evidence_controller_does_not_spend_visual_budget_on_wrong_build_media():
    assets = [
        _asset("current-build", "text", eligible=True),
        _asset("wrong-build", "video", eligible=False),
        _asset("wrong-build-audio", "audio", eligible=False),
    ]
    controller = ScopedEvidenceController()

    plan = controller.plan(
        "为什么当前 build 的日志报错？",
        assets,
        retriever_enabled=True,
    )

    assert plan.needs_text is True
    assert plan.needs_visual is False
    assert plan.needs_audio is False
