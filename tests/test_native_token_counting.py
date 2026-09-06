from __future__ import annotations

import asyncio

import pytest

from worldforge.providers.anthropic import AnthropicProvider
from worldforge.providers.gemini import GeminiProvider
from worldforge.providers.native_tokens import decide_native_token_count
from worldforge.providers.base import ProviderError


class _Response:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


def test_native_token_auto_skips_without_trustworthy_limit():
    decision = decide_native_token_count(
        "gemini",
        messages=[{"role": "user", "content": "hello"}],
        media_items=1,
        safe_input_tokens=None,
        limit_source=None,
        environ={"LINGJING_GEMINI_NATIVE_TOKEN_COUNT": "auto"},
    )
    assert decision.should_count is False
    assert decision.reason == "no-trustworthy-input-limit"


def test_native_token_auto_counts_media_when_limit_is_known():
    decision = decide_native_token_count(
        "gemini",
        messages=[{"role": "user", "content": "hello"}],
        media_items=1,
        safe_input_tokens=100_000,
        limit_source="model-metadata",
        environ={"LINGJING_GEMINI_NATIVE_TOKEN_COUNT": "auto"},
    )
    assert decision.should_count is True
    assert decision.reason == "media-token-cost-needs-native-count"


def test_gemini_native_metadata_and_count_are_used(monkeypatch):
    calls: list[tuple[str, str]] = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, url, **_kwargs):
            calls.append(("GET", url))
            return _Response(
                200,
                {"inputTokenLimit": 1000, "outputTokenLimit": 200},
            )

        async def post(self, url, **_kwargs):
            calls.append(("POST", url))
            if ":countTokens" in url:
                return _Response(
                    200,
                    {
                        "totalTokens": 41,
                        "promptTokensDetails": [
                            {"modality": "TEXT", "tokenCount": 41}
                        ],
                    },
                )
            return _Response(
                200,
                {
                    "candidates": [
                        {"content": {"parts": [{"text": "gemini-ok"}]}}
                    ]
                },
            )

    monkeypatch.setenv("LINGJING_GEMINI_NATIVE_TOKEN_COUNT", "on")
    monkeypatch.delenv("LINGJING_GEMINI_CONTEXT_WINDOW_TOKENS", raising=False)
    monkeypatch.setattr(
        "worldforge.providers.gemini.httpx.AsyncClient",
        lambda **_kwargs: Client(),
    )

    provider = GeminiProvider("secret", "gemini-test")
    answer = asyncio.run(
        provider.chat(
            messages=[{"role": "user", "content": "测试 token"}],
            assets=[],
            max_tokens=50,
        )
    )
    telemetry = provider.request_telemetry()

    assert answer == "gemini-ok"
    assert [method for method, _url in calls] == ["GET", "POST", "POST"]
    assert ":countTokens" in calls[1][1]
    assert ":generateContent" in calls[2][1]
    assert telemetry["native_token_count_status"] == "success"
    assert telemetry["native_token_count_input_tokens"] == 41
    assert telemetry["native_token_safe_input_tokens"] == 1000
    assert telemetry["native_token_limit_source"] == "gemini-models.get"
    assert telemetry["native_token_count_modality_details"][0]["modality"] == "TEXT"


def test_gemini_native_count_blocks_generation_over_limit(monkeypatch):
    calls: list[tuple[str, str]] = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, url, **_kwargs):
            calls.append(("GET", url))
            return _Response(200, {"inputTokenLimit": 20, "outputTokenLimit": 10})

        async def post(self, url, **_kwargs):
            calls.append(("POST", url))
            if ":countTokens" in url:
                return _Response(200, {"totalTokens": 21})
            raise AssertionError("generation must not be sent after native over-limit")

    monkeypatch.setenv("LINGJING_GEMINI_NATIVE_TOKEN_COUNT", "on")
    monkeypatch.delenv("LINGJING_GEMINI_CONTEXT_WINDOW_TOKENS", raising=False)
    monkeypatch.setattr(
        "worldforge.providers.gemini.httpx.AsyncClient",
        lambda **_kwargs: Client(),
    )

    provider = GeminiProvider("secret", "gemini-small")
    with pytest.raises(ProviderError, match="21 > 20"):
        asyncio.run(
            provider.chat(
                messages=[{"role": "user", "content": "too large"}],
                assets=[],
                max_tokens=5,
            )
        )
    assert len(calls) == 2
    assert provider.request_telemetry()["native_token_count_status"] == "blocked-over-limit"


def test_anthropic_native_count_fail_open_then_generates(monkeypatch):
    calls: list[tuple[str, str]] = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, url, **_kwargs):
            calls.append(("GET", url))
            return _Response(200, {"max_input_tokens": 1000, "max_tokens": 200})

        async def post(self, url, **_kwargs):
            calls.append(("POST", url))
            if url.endswith("/count_tokens"):
                return _Response(503, {"error": "temporarily unavailable"})
            return _Response(
                200,
                {"content": [{"type": "text", "text": "claude-ok"}]},
            )

    monkeypatch.setenv("LINGJING_ANTHROPIC_NATIVE_TOKEN_COUNT", "on")
    monkeypatch.delenv("LINGJING_ANTHROPIC_CONTEXT_WINDOW_TOKENS", raising=False)
    monkeypatch.setattr(
        "worldforge.providers.anthropic.httpx.AsyncClient",
        lambda **_kwargs: Client(),
    )

    provider = AnthropicProvider("secret", "claude-test")
    answer = asyncio.run(
        provider.chat(
            messages=[{"role": "user", "content": "hello"}],
            assets=[],
            max_tokens=50,
        )
    )
    telemetry = provider.request_telemetry()

    assert answer == "claude-ok"
    assert [method for method, _url in calls] == ["GET", "POST", "POST"]
    assert calls[1][1].endswith("/v1/messages/count_tokens")
    assert calls[2][1].endswith("/v1/messages")
    assert telemetry["native_token_count_status"] == "fallback-http-503"
    assert telemetry["native_token_limit_source"] == "anthropic-models.get"


def test_anthropic_model_limit_is_cached_across_requests(monkeypatch):
    calls: list[tuple[str, str]] = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, url, **_kwargs):
            calls.append(("GET", url))
            return _Response(200, {"max_input_tokens": 5000, "max_tokens": 500})

        async def post(self, url, **_kwargs):
            calls.append(("POST", url))
            if url.endswith("/count_tokens"):
                return _Response(200, {"input_tokens": 20})
            return _Response(200, {"content": [{"type": "text", "text": "ok"}]})

    monkeypatch.setenv("LINGJING_ANTHROPIC_NATIVE_TOKEN_COUNT", "on")
    monkeypatch.delenv("LINGJING_ANTHROPIC_CONTEXT_WINDOW_TOKENS", raising=False)
    monkeypatch.setattr(
        "worldforge.providers.anthropic.httpx.AsyncClient",
        lambda **_kwargs: Client(),
    )

    provider = AnthropicProvider("secret", "claude-cache-test")
    for _ in range(2):
        assert asyncio.run(
            provider.chat(
                messages=[{"role": "user", "content": "hello"}],
                assets=[],
                max_tokens=50,
            )
        ) == "ok"

    assert sum(1 for method, _url in calls if method == "GET") == 1
    assert sum(1 for method, url in calls if method == "POST" and url.endswith("/count_tokens")) == 2


def test_product_exports_native_token_telemetry_wrapper():
    from worldforge.product import ProductAnalyzer

    assert ProductAnalyzer.__module__.endswith("contextual_analyzer_v3")
