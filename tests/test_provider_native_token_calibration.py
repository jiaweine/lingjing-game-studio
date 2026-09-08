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


def test_gemini_exact_count_records_rtt_and_estimate_calibration(monkeypatch):
    calls: list[str] = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, url, **_kwargs):
            calls.append(url)
            if ":countTokens?" in url:
                return _Response(
                    200,
                    {
                        "totalTokens": 42,
                        "promptTokensDetails": [
                            {"modality": "TEXT", "tokenCount": 42}
                        ],
                    },
                )
            return _Response(
                200,
                {
                    "candidates": [
                        {"content": {"parts": [{"text": "ok"}]}}
                    ]
                },
            )

    monkeypatch.setenv("LINGJING_GEMINI_NATIVE_TOKEN_COUNT", "on")
    monkeypatch.setenv("LINGJING_GEMINI_CONTEXT_WINDOW_TOKENS", "1000")
    monkeypatch.setenv("LINGJING_GEMINI_OUTPUT_RESERVE_TOKENS", "100")
    monkeypatch.setattr(
        "worldforge.providers.gemini.httpx.AsyncClient",
        lambda **_kwargs: Client(),
    )
    provider = GeminiProvider("key", "gemini-test")

    async def run():
        answer = await provider.chat(
            messages=[{"role": "user", "content": "hello exact count"}],
            max_tokens=100,
        )
        return answer, provider.request_telemetry()

    answer, telemetry = asyncio.run(run())
    assert answer == "ok"
    assert len(calls) == 2
    assert telemetry["native_token_count_status"] == "success"
    assert telemetry["native_token_count_input_tokens"] == 42
    assert telemetry["native_token_count_extra_rtt_ms"] >= 0.0
    assert telemetry["native_token_estimate_delta"] is not None
    assert telemetry["native_token_exact_estimate_ratio"] > 0.0
    assert telemetry["native_token_count_modality_details"][0]["modality"] == "TEXT"


def test_claude_exact_count_records_rtt_and_estimate_calibration(monkeypatch):
    calls: list[str] = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, url, **_kwargs):
            calls.append(url)
            if url.endswith("/v1/messages/count_tokens"):
                return _Response(200, {"input_tokens": 37})
            return _Response(
                200,
                {"content": [{"type": "text", "text": "ok"}]},
            )

    monkeypatch.setenv("LINGJING_ANTHROPIC_NATIVE_TOKEN_COUNT", "on")
    monkeypatch.setenv("LINGJING_ANTHROPIC_CONTEXT_WINDOW_TOKENS", "1000")
    monkeypatch.setenv("LINGJING_ANTHROPIC_OUTPUT_RESERVE_TOKENS", "100")
    monkeypatch.setattr(
        "worldforge.providers.anthropic.httpx.AsyncClient",
        lambda **_kwargs: Client(),
    )
    provider = AnthropicProvider("key", "claude-test")

    async def run():
        answer = await provider.chat(
            messages=[{"role": "user", "content": "hello exact count"}],
            max_tokens=100,
        )
        return answer, provider.request_telemetry()

    answer, telemetry = asyncio.run(run())
    assert answer == "ok"
    assert calls == [
        "https://api.anthropic.com/v1/messages/count_tokens",
        "https://api.anthropic.com/v1/messages",
    ]
    assert telemetry["native_token_count_status"] == "success"
    assert telemetry["native_token_count_input_tokens"] == 37
    assert telemetry["native_token_count_extra_rtt_ms"] >= 0.0
    assert telemetry["native_token_estimate_delta"] is not None
    assert telemetry["native_token_exact_estimate_ratio"] > 0.0
