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
    # score() may still be invoked with an empty list, but no acoustic item is dispatched.
    assert audio.calls and audio.calls[0]["items"] == []
    assert response["hits"][0]["asset_id"] == "img"


@pytest.mark.asyncio
async def test_audio_query_routes_audio_and_video_audio_to_lco(monkeypatch):
    visual = _Worker(coordinator.WorkerResult("wemm", [], 8.0, "disabled"))
    audio = _Worker(
        coordinator.WorkerResult(
            "lco:test",
            [
                coordinator.WorkerScore("wav", 0.93, "lco:test", "audio"),
                coordinator.WorkerScore("vid", 0.82, "lco:test", "video_with_audio"),
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
