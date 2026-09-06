from __future__ import annotations

import asyncio

import pytest

from worldforge.context.preindex import PreindexScheduler
from worldforge.context.retrieval_sidecar import MultimodalIndexResult


class _Client:
    enabled = True

    def __init__(self):
        self.calls = []
        self.active = 0
        self.max_active = 0

    async def preindex(self, assets, *, include_audio=False):
        self.calls.append((assets, include_audio))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.01)
        self.active -= 1
        return MultimodalIndexResult(True, 10.0)


def _asset(asset_id: str, kind: str, tmp_path, *, duration=0.0):
    path = tmp_path / f"{asset_id}.bin"
    path.write_bytes(asset_id.encode())
    mime = {
        "text": "text/plain",
        "video": "video/mp4",
        "audio": "audio/wav",
        "image": "image/png",
    }[kind]
    return {
        "id": asset_id,
        "name": path.name,
        "mime": mime,
        "path": str(path),
        "meta": {"kind": kind, "duration": duration},
    }


@pytest.mark.asyncio
async def test_scheduler_deduplicates_same_source_fingerprint(tmp_path):
    scheduler = PreindexScheduler()
    scheduler.enabled = True
    scheduler.retry_seconds = 3600.0
    client = _Client()
    asset = _asset("log", "text", tmp_path)

    assert scheduler.schedule(client, [asset]) is True
    await asyncio.gather(*list(scheduler._tasks))
    assert scheduler.schedule(client, [asset]) is False
    assert len(client.calls) == 1
    assert scheduler.completed == 1


@pytest.mark.asyncio
async def test_scheduler_serializes_expensive_warmups(tmp_path):
    scheduler = PreindexScheduler()
    scheduler.enabled = True
    scheduler.max_concurrency = 1
    client = _Client()

    assert scheduler.schedule(client, [_asset("a", "text", tmp_path)])
    assert scheduler.schedule(client, [_asset("b", "video", tmp_path, duration=600.0)])
    await asyncio.gather(*list(scheduler._tasks))

    assert client.max_active == 1
    assert scheduler.completed == 2


@pytest.mark.asyncio
async def test_scheduler_does_not_warm_trivial_media_and_audio_is_explicit(tmp_path):
    scheduler = PreindexScheduler()
    scheduler.enabled = True
    client = _Client()

    image = _asset("img", "image", tmp_path)
    short_video = _asset("short", "video", tmp_path, duration=20.0)
    assert scheduler.schedule(client, [image, short_video]) is False

    audio = _asset("voice", "audio", tmp_path, duration=180.0)
    assert scheduler.schedule(client, [audio]) is True
    await asyncio.gather(*list(scheduler._tasks))
    assert client.calls[-1][1] is True
