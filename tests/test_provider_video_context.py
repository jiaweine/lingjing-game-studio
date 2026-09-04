from __future__ import annotations

import pytest

from worldforge.providers.openai_compat import OpenAICompatProvider
import worldforge.providers.openai_compat as compat


class _Response:
    status_code = 200
    text = ""

    def json(self):
        return {"choices": [{"message": {"content": "ok"}}]}


class _Client:
    payload = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, _url, *, headers, json):
        type(self).payload = json
        return _Response()


@pytest.mark.asyncio
async def test_video_capable_openai_compat_provider_sends_video_url(monkeypatch, tmp_path):
    monkeypatch.setattr(compat.httpx, "AsyncClient", _Client)
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video-fixture")
    provider = OpenAICompatProvider(
        key="qwen",
        name="Qwen",
        vendor="test",
        api_key="test-key",
        base_url="https://example.invalid/v1",
        model="qwen3-vl-plus",
        multimodal=True,
        supports_video=True,
    )

    result = await provider.chat(
        messages=[{"role": "user", "content": "分析视频"}],
        assets=[
            {
                "id": "video-1",
                "name": "clip.mp4",
                "mime": "video/mp4",
                "path": str(video),
                "meta": {"kind": "video"},
            }
        ],
    )

    assert result == "ok"
    content = _Client.payload["messages"][-1]["content"]
    video_parts = [item for item in content if item.get("type") == "video_url"]
    assert len(video_parts) == 1
    assert video_parts[0]["video_url"]["url"].startswith("data:video/mp4;base64,")
    assert video_parts[0]["fps"] == 1
