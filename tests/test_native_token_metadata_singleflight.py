from __future__ import annotations

import asyncio

from worldforge.providers.anthropic import AnthropicProvider
from worldforge.providers.gemini import GeminiProvider


class _Response:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


def test_gemini_model_metadata_cold_start_is_singleflight(monkeypatch):
    monkeypatch.delenv("LINGJING_GEMINI_CONTEXT_WINDOW_TOKENS", raising=False)
    calls = 0

    class Client:
        async def get(self, _url, **_kwargs):
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.02)
            return _Response(
                200,
                {"inputTokenLimit": 8192, "outputTokenLimit": 1024},
            )

    provider = GeminiProvider("secret", "gemini-singleflight")
    client = Client()

    async def run():
        rows = await asyncio.gather(
            *[
                provider._resolve_native_input_limit(client, max_tokens=256)
                for _ in range(8)
            ]
        )
        cached = await provider._resolve_native_input_limit(client, max_tokens=256)
        return rows, cached

    rows, cached = asyncio.run(run())
    assert calls == 1
    assert len(set(rows)) == 1
    assert rows[0] == (8192, "gemini-models.get", 8192, 1024)
    assert cached == rows[0]


def test_anthropic_model_metadata_cold_start_is_singleflight(monkeypatch):
    monkeypatch.delenv("LINGJING_ANTHROPIC_CONTEXT_WINDOW_TOKENS", raising=False)
    calls = 0

    class Client:
        async def get(self, _url, **_kwargs):
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.02)
            return _Response(
                200,
                {"max_input_tokens": 200_000, "max_tokens": 8192},
            )

    provider = AnthropicProvider("secret", "claude-singleflight")
    client = Client()

    async def run():
        rows = await asyncio.gather(
            *[
                provider._resolve_native_input_limit(client, max_tokens=512)
                for _ in range(8)
            ]
        )
        cached = await provider._resolve_native_input_limit(client, max_tokens=512)
        return rows, cached

    rows, cached = asyncio.run(run())
    assert calls == 1
    assert len(set(rows)) == 1
    assert rows[0] == (200_000, "anthropic-models.get", 200_000, 8192)
    assert cached == rows[0]


def test_metadata_failure_is_singleflight_and_negative_cached(monkeypatch):
    monkeypatch.delenv("LINGJING_GEMINI_CONTEXT_WINDOW_TOKENS", raising=False)
    calls = 0

    class Client:
        async def get(self, _url, **_kwargs):
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.02)
            return _Response(503, {"error": "unavailable"})

    provider = GeminiProvider("secret", "gemini-singleflight-failure")
    client = Client()

    async def run():
        rows = await asyncio.gather(
            *[
                provider._resolve_native_input_limit(client, max_tokens=256)
                for _ in range(8)
            ]
        )
        cached = await provider._resolve_native_input_limit(client, max_tokens=256)
        return rows, cached

    rows, cached = asyncio.run(run())
    expected = (None, "gemini-model-metadata-unavailable", None, None)
    assert calls == 1
    assert rows == [expected] * 8
    assert cached == expected
