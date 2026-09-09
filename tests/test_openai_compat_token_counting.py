from __future__ import annotations

import asyncio

from worldforge.providers.base import ProviderError
from worldforge.providers.openai_compat import OpenAICompatProvider


class _Response:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


def _provider() -> OpenAICompatProvider:
    return OpenAICompatProvider(
        key="custom",
        name="Custom",
        vendor="Private",
        api_key=None,
        base_url="http://gateway.test/v1",
        model="private-model",
        multimodal=False,
        auth_optional=True,
    )


def test_trusted_compatible_gateway_exact_count_records_calibration(monkeypatch):
    calls: list[str] = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, url, **_kwargs):
            calls.append(url)
            if url.endswith("/token/count"):
                return _Response(200, {"usage": {"input_tokens": 42}})
            return _Response(
                200,
                {"choices": [{"message": {"content": "ok"}}]},
            )

    monkeypatch.setenv("LINGJING_CUSTOM_TOKEN_COUNT_URL", "/token/count")
    monkeypatch.setenv("LINGJING_CUSTOM_TOKEN_COUNT_RESPONSE_FIELD", "usage.input_tokens")
    monkeypatch.setenv("LINGJING_CUSTOM_TOKEN_COUNT_TRUSTED", "1")
    monkeypatch.setenv("LINGJING_CUSTOM_NATIVE_TOKEN_COUNT", "on")
    monkeypatch.setenv("LINGJING_CUSTOM_CONTEXT_WINDOW_TOKENS", "1000")
    # This case measures calibration, not over-limit blocking. Keep the operator profile
    # internally consistent instead of inheriting the production default 1800-token reserve,
    # which would intentionally leave no usable input window for a 1000-token model.
    monkeypatch.setenv("LINGJING_CUSTOM_OUTPUT_RESERVE_TOKENS", "100")
    monkeypatch.setattr(
        "worldforge.providers.openai_compat.httpx.AsyncClient",
        lambda **_kwargs: Client(),
    )

    provider = _provider()

    async def run():
        answer = await provider.chat(
            messages=[{"role": "user", "content": "hello exact count"}],
            max_tokens=100,
        )
        return answer, provider.request_telemetry()

    answer, telemetry = asyncio.run(run())
    assert answer == "ok"
    assert calls == [
        "http://gateway.test/v1/token/count",
        "http://gateway.test/v1/chat/completions",
    ]
    assert telemetry["native_token_count_status"] == "success"
    assert telemetry["native_token_count_input_tokens"] == 42
    assert telemetry["native_token_count_endpoint_status"] == "trusted-configured"
    assert telemetry["native_token_count_response_field"] == "usage.input_tokens"
    assert telemetry["native_token_count_extra_rtt_ms"] >= 0.0
    assert telemetry["native_token_exact_estimate_ratio"] > 0.0
    assert telemetry["native_token_estimate_delta"] is not None


def test_configured_but_untrusted_gateway_never_receives_count_request(monkeypatch):
    calls: list[str] = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, url, **_kwargs):
            calls.append(url)
            if url.endswith("/token/count"):
                raise AssertionError("untrusted count endpoint must not be called")
            return _Response(
                200,
                {"choices": [{"message": {"content": "ok"}}]},
            )

    monkeypatch.setenv("LINGJING_CUSTOM_TOKEN_COUNT_URL", "/token/count")
    monkeypatch.setenv("LINGJING_CUSTOM_TOKEN_COUNT_TRUSTED", "0")
    monkeypatch.setenv("LINGJING_CUSTOM_NATIVE_TOKEN_COUNT", "on")
    monkeypatch.setattr(
        "worldforge.providers.openai_compat.httpx.AsyncClient",
        lambda **_kwargs: Client(),
    )

    provider = _provider()

    async def run():
        answer = await provider.chat(
            messages=[{"role": "user", "content": "hello"}],
            max_tokens=100,
        )
        return answer, provider.request_telemetry()

    answer, telemetry = asyncio.run(run())
    assert answer == "ok"
    assert calls == ["http://gateway.test/v1/chat/completions"]
    assert telemetry["native_token_count_endpoint_status"] == "configured-but-untrusted"
    assert telemetry["native_token_count_status"] == "skipped-no-trusted-endpoint"


def test_trusted_exact_count_blocks_confirmed_over_limit_before_generation(monkeypatch):
    calls: list[str] = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, url, **_kwargs):
            calls.append(url)
            if url.endswith("/token/count"):
                return _Response(200, {"input_tokens": 95})
            raise AssertionError("generation must not be sent after exact over-limit")

    monkeypatch.setenv("LINGJING_CUSTOM_TOKEN_COUNT_URL", "/token/count")
    monkeypatch.setenv("LINGJING_CUSTOM_TOKEN_COUNT_TRUSTED", "1")
    monkeypatch.setenv("LINGJING_CUSTOM_TOKEN_COUNT_RESPONSE_FIELD", "input_tokens")
    monkeypatch.setenv("LINGJING_CUSTOM_NATIVE_TOKEN_COUNT", "on")
    monkeypatch.setenv("LINGJING_CUSTOM_CONTEXT_WINDOW_TOKENS", "100")
    monkeypatch.setenv("LINGJING_CUSTOM_OUTPUT_RESERVE_TOKENS", "10")
    monkeypatch.setattr(
        "worldforge.providers.openai_compat.httpx.AsyncClient",
        lambda **_kwargs: Client(),
    )

    provider = _provider()

    async def run():
        try:
            await provider.chat(
                messages=[{"role": "user", "content": "too large"}],
                max_tokens=10,
            )
        except ProviderError as exc:
            return exc, provider.request_telemetry()
        raise AssertionError("expected ProviderError")

    error, telemetry = asyncio.run(run())
    assert "95 > 90" in str(error)
    assert calls == ["http://gateway.test/v1/token/count"]
    assert telemetry["native_token_count_status"] == "blocked-over-limit"
