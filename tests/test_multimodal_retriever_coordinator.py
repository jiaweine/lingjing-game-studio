from __future__ import annotations

import pytest

from services.multimodal_retriever import app as coordinator


class _Worker:
    def __init__(self, result):
        self.result = result
        self.calls = []
        self.enabled = True

    async def score(self, *, query, items, backend_hint):
        self.calls.append(
            {"query": query, "items": items, "backend_hint": backend_hint}
        )
        return self.result


class _Indexer:
    def __init__(self, backend):
        self.backend = backend
        self.calls = []
        self.enabled = True

    async def index(self, *, items, backend_hint):
        self.calls.append({"items": items, "backend_hint": backend_hint})
        return coordinator.WorkerIndexResult(
            self.backend,
            True,
            5.0,
            {"indexed": len(items)},
        )


def test_temporal_detection_requires_real_time_expression():
    assert coordinator._temporal_query("37 秒附近发生了什么") is True
    assert coordinator._temporal_query("检查 1:20 附近") is True
    assert coordinator._temporal_query("look around 42 sec") is True
    assert coordinator._temporal_query("build 1.4.7 shield_race") is False
    assert coordinator._temporal_query("version 2.1.0 screenshot") is False


def test_cosine_calibration_does_not_turn_tiny_gap_into_false_certainty():
    rows = [
        coordinator.WorkerScore("a", 0.51, "wemm", "image"),
        coordinator.WorkerScore("b", 0.50, "wemm", "image"),
    ]
    normalized = coordinator._normalize_backend(rows)

    assert normalized["a"] > normalized["b"]
    assert normalized["a"] - normalized["b"] < 0.12
    assert normalized["b"] > 0.70


@pytest.mark.asyncio
async def test_visual_query_does_not_spend_audio_worker_gpu(monkeypatch):
    visual = _Worker(
        coordinator.WorkerResult(
            "wemm:test:256d",
            [coordinator.WorkerScore("img", 0.91, "wemm:test:256d", "image")],
            12.0,
        )
    )
    audio = _Worker(
        coordinator.WorkerResult(
            "lco:test",
            [coordinator.WorkerScore("vid", 0.99, "lco:test", "video_with_audio")],
            20.0,
        )
    )
    monkeypatch.setattr(coordinator, "VISUAL_WORKER", visual)
    monkeypatch.setattr(coordinator, "AUDIO_WORKER", audio)

    response = await coordinator.rank(
        coordinator.RankRequest(
            query="比较两张截图里 Boss 护盾图标",
            assets=[
                coordinator.AssetDescriptor(
                    id="img", name="boss.png", mime="image/png", path="/tmp/boss.png",
                    meta={"kind": "image"},
                ),
                coordinator.AssetDescriptor(
                    id="vid", name="run.mp4", mime="video/mp4", path="/tmp/run.mp4",
                    meta={"kind": "video", "has_audio": True},
                ),
            ],
        )
    )

    assert visual.calls and visual.calls[0]["items"]
    assert audio.calls and audio.calls[0]["items"] == []
    assert response["hits"][0]["asset_id"] == "img"
    assert response["debug"]["temporal_query"] is False


@pytest.mark.asyncio
async def test_audio_query_routes_audio_and_video_audio_to_lco(monkeypatch):
    visual = _Worker(coordinator.WorkerResult("wemm", [], 8.0, "disabled"))
    audio = _Worker(
        coordinator.WorkerResult(
            "lco:test",
            [
                coordinator.WorkerScore("wav", 0.93, "lco:test", "audio"),
                coordinator.WorkerScore(
                    "vid", 0.82, "lco:test", "video_with_audio", start=36.0, end=42.0
                ),
            ],
            21.0,
        )
    )
    monkeypatch.setattr(coordinator, "VISUAL_WORKER", visual)
    monkeypatch.setattr(coordinator, "AUDIO_WORKER", audio)

    response = await coordinator.rank(
        coordinator.RankRequest(
            query="37 秒附近的音效是不是重复触发？",
            assets=[
                coordinator.AssetDescriptor(
                    id="wav", name="boss.wav", mime="audio/wav", path="/tmp/boss.wav",
                    meta={"kind": "audio", "duration": 20.0},
                ),
                coordinator.AssetDescriptor(
                    id="vid", name="run.mp4", mime="video/mp4", path="/tmp/run.mp4",
                    meta={"kind": "video", "has_audio": True, "duration": 90.0},
                ),
            ],
        )
    )

    modalities = {item["modality"] for item in audio.calls[0]["items"]}
    assert modalities == {"audio", "video_with_audio"}
    assert response["hits"][0]["asset_id"] == "wav"
    assert response["debug"]["audio_query"] is True
    assert response["debug"]["temporal_query"] is True


@pytest.mark.asyncio
async def test_strongest_semantic_specialist_owns_localization(monkeypatch):
    visual = _Worker(
        coordinator.WorkerResult(
            "wemm",
            [coordinator.WorkerScore("vid", 0.40, "wemm", "video", start=10.0, end=30.0)],
            10.0,
        )
    )
    audio = _Worker(
        coordinator.WorkerResult(
            "lco",
            [
                coordinator.WorkerScore(
                    "vid", 0.95, "lco", "video_with_audio", start=72.0, end=84.0
                )
            ],
            20.0,
        )
    )
    monkeypatch.setattr(coordinator, "VISUAL_WORKER", visual)
    monkeypatch.setattr(coordinator, "AUDIO_WORKER", audio)

    response = await coordinator.rank(
        coordinator.RankRequest(
            query="哪一段音效重复了？",
            assets=[
                coordinator.AssetDescriptor(
                    id="vid", name="run.mp4", mime="video/mp4", path="/tmp/run.mp4",
                    meta={"kind": "video", "has_audio": True, "duration": 120.0},
                )
            ],
        )
    )

    hit = response["hits"][0]
    assert hit["start"] == 72.0
    assert hit["end"] == 84.0


@pytest.mark.asyncio
async def test_preindex_uses_only_explicit_indexer_replicas(monkeypatch):
    visual_indexer = _Indexer("wemm-indexer")
    audio_indexer = _Indexer("lco-indexer")
    monkeypatch.setattr(coordinator, "VISUAL_INDEXER", visual_indexer)
    monkeypatch.setattr(coordinator, "AUDIO_INDEXER", audio_indexer)

    response = await coordinator.index(
        coordinator.IndexRequest(
            include_audio=False,
            assets=[
                coordinator.AssetDescriptor(
                    id="video", name="run.mp4", mime="video/mp4", path="/tmp/run.mp4",
                    meta={"kind": "video", "has_audio": True, "duration": 600.0},
                ),
                coordinator.AssetDescriptor(
                    id="log", name="runtime.log", mime="text/plain", path="/tmp/runtime.log",
                    meta={"kind": "text"},
                ),
            ],
        )
    )

    assert response["accepted"] is True
    assert {item["modality"] for item in visual_indexer.calls[0]["items"]} == {
        "video", "text_file"
    }
    assert audio_indexer.calls[0]["items"] == []


@pytest.mark.asyncio
async def test_audio_preindex_is_opt_in(monkeypatch):
    visual_indexer = _Indexer("wemm-indexer")
    audio_indexer = _Indexer("lco-indexer")
    monkeypatch.setattr(coordinator, "VISUAL_INDEXER", visual_indexer)
    monkeypatch.setattr(coordinator, "AUDIO_INDEXER", audio_indexer)

    await coordinator.index(
        coordinator.IndexRequest(
            include_audio=True,
            assets=[
                coordinator.AssetDescriptor(
                    id="audio", name="voice.wav", mime="audio/wav", path="/tmp/voice.wav",
                    meta={"kind": "audio", "duration": 180.0},
                )
            ],
        )
    )

    assert [item["modality"] for item in audio_indexer.calls[0]["items"]] == ["audio"]


@pytest.mark.asyncio
async def test_coordinator_returns_lexical_fallback_when_gpu_workers_fail(monkeypatch):
    failed_visual = _Worker(coordinator.WorkerResult("wemm", [], 900.0, "Timeout"))
    failed_audio = _Worker(coordinator.WorkerResult("lco", [], 900.0, "Timeout"))
    monkeypatch.setattr(coordinator, "VISUAL_WORKER", failed_visual)
    monkeypatch.setattr(coordinator, "AUDIO_WORKER", failed_audio)

    response = await coordinator.rank(
        coordinator.RankRequest(
            query="build 1.4.7 shield_race",
            assets=[
                coordinator.AssetDescriptor(
                    id="right",
                    name="shield_race-build-1.4.7.mp4",
                    mime="video/mp4",
                    path="/tmp/right.mp4",
                    meta={"kind": "video", "build": "1.4.7"},
                ),
                coordinator.AssetDescriptor(
                    id="wrong",
                    name="unrelated.mp4",
                    mime="video/mp4",
                    path="/tmp/wrong.mp4",
                    meta={"kind": "video"},
                ),
            ],
        )
    )

    assert response["backend"] == "lexical-fallback"
    assert response["hits"][0]["asset_id"] == "right"
    assert response["debug"]["temporal_query"] is False
