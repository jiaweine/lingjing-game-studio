from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from worldforge.product.store import DEMO_USER_ID, DEMO_WORKSPACE_ID
from worldforge.storage import ObjectStorage, _asset_bundle_prefix


class _Upload:
    filename = "capture.mp4"
    content_type = "video/mp4"

    def __init__(self) -> None:
        self._chunks = [b"fake-video-bytes", b""]

    async def read(self, _size: int) -> bytes:
        return self._chunks.pop(0)


class _FailingStorage(ObjectStorage):
    name = "fake"

    def __init__(self) -> None:
        self.put_count = 0
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []
        self.deleted_prefixes: list[str] = []

    def _put_file(self, key, source, _content_type):
        self.put_count += 1
        if self.put_count == 2:
            raise RuntimeError("injected frame upload failure")
        self.objects[str(key)] = Path(source).read_bytes()
        return key

    def delete(self, key):
        self.deleted.append(str(key))
        self.objects.pop(str(key), None)

    def delete_prefix(self, prefix):
        prefix = str(prefix)
        self.deleted_prefixes.append(prefix)
        for key in list(self.objects):
            if key.startswith(prefix):
                self.deleted.append(key)
                self.objects.pop(key, None)


@pytest.mark.asyncio
async def test_asset_upload_rolls_back_source_when_frame_upload_fails(monkeypatch):
    app_module = importlib.import_module("worldforge.api.app")
    storage = _FailingStorage()

    monkeypatch.setattr(app_module, "storage", storage)
    monkeypatch.setattr(
        app_module,
        "probe_media",
        lambda _path, _mime: {"valid": True, "kind": "video", "duration": 1.0},
    )

    def fake_frames(_source, output_dir, _count):
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        frames = [output_dir / "00.jpg", output_dir / "01.jpg"]
        for index, frame in enumerate(frames):
            frame.write_bytes(f"frame-{index}".encode())
        return frames

    monkeypatch.setattr(app_module, "extract_video_frames", fake_frames)

    request = SimpleNamespace(state=SimpleNamespace(request_id="asset-rollback-test"))
    principal = SimpleNamespace(
        workspace_id=DEMO_WORKSPACE_ID,
        user_id=DEMO_USER_ID,
    )

    with pytest.raises(RuntimeError, match="injected frame upload failure"):
        await app_module.asset_upload(
            request,
            file=_Upload(),
            conversation_id=None,
            principal=principal,
        )

    assert storage.put_count == 2
    assert storage.objects == {}
    assert len(storage.deleted_prefixes) == 1
    assert storage.deleted_prefixes[0].startswith(f"{DEMO_WORKSPACE_ID}/assets/")
    assert len(storage.deleted) == 1
    assert storage.deleted[0].endswith("/source.mp4")


def test_asset_bundle_prefix_is_strictly_scoped_to_generated_asset_namespace():
    asset_id = "a" * 32
    assert _asset_bundle_prefix(
        f"workspace/assets/{asset_id}/frames/00.jpg"
    ) == f"workspace/assets/{asset_id}/"
    assert _asset_bundle_prefix("workspace/assets/not-a-generated-id/source.bin") is None
    assert _asset_bundle_prefix(f"workspace/exports/{asset_id}/source.bin") is None
    assert _asset_bundle_prefix(f"/workspace/assets/{asset_id}/source.bin") is None
