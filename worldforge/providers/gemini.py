from __future__ import annotations

import base64
from pathlib import Path
import time
from typing import Any

import httpx

from .base import BaseProvider, ProviderError, ProviderInfo
from .context_budget import load_provider_context_budget
from .native_tokens import (
    decide_native_token_count,
    native_count_exceeds_limit,
    native_token_mode,
)

_MAX_MEDIA_ITEMS = 6
_MAX_SINGLE_MEDIA_BYTES = 18 * 1024 * 1024
_MAX_TOTAL_MEDIA_BYTES = 32 * 1024 * 1024
_MODEL_LIMIT_CACHE_SECONDS = 3600.0
_MODEL_LIMIT_FAILURE_CACHE_SECONDS = 60.0


class GeminiProvider(BaseProvider):
    def __init__(self, api_key, model):
        self.api_key, self.model = api_key, model
        self._native_limits_cache: tuple[float, int | None, int | None, str] | None = None
        self.info = ProviderInfo(
            "gemini",
            "Gemini",
            "Google",
            model,
            bool(api_key and model),
            True,
            True,
            True,
            "支持图片、视频、音频和长文档理解",
        )

    @property
    def _model_id(self) -> str:
        return str(self.model or "").removeprefix("models/")

    async def _resolve_native_input_limit(
        self,
        client: httpx.AsyncClient,
        *,
        max_tokens: int,
    ) -> tuple[int | None, str | None, int | None, int | None]:
        profile = load_provider_context_budget("gemini", model=self.model)
        if profile.context_window_tokens:
            # Operator-declared CONTEXT_WINDOW_TOKENS keeps the historical combined-window
            # semantics used by ContextOS, so reserve requested output before accepting input.
            safe = max(
                1,
                int(profile.context_window_tokens)
                - max(int(profile.output_reserve_tokens), int(max_tokens)),
            )
            return safe, "operator-context-profile", profile.context_window_tokens, None

        now = time.monotonic()
        cached = self._native_limits_cache
        if cached and cached[0] > now:
            _expires, input_limit, output_limit, source = cached
            return input_limit, source, input_limit, output_limit

        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self._model_id}?key={self.api_key}"
        )
        try:
            response = await client.get(url, timeout=5)
            if response.status_code >= 400:
                raise ValueError(f"http-{response.status_code}")
            payload = response.json()
            input_limit = int(payload.get("inputTokenLimit") or 0) or None
            output_limit = int(payload.get("outputTokenLimit") or 0) or None
            if input_limit is None:
                raise ValueError("missing-inputTokenLimit")
        except (httpx.HTTPError, TypeError, ValueError):
            self._native_limits_cache = (
                now + _MODEL_LIMIT_FAILURE_CACHE_SECONDS,
                None,
                None,
                "gemini-model-metadata-unavailable",
            )
            return None, "gemini-model-metadata-unavailable", None, None

        self._native_limits_cache = (
            now + _MODEL_LIMIT_CACHE_SECONDS,
            input_limit,
            output_limit,
            "gemini-models.get",
        )
        return input_limit, "gemini-models.get", input_limit, output_limit

    def _build_parts(
        self,
        messages: list[dict[str, Any]],
        assets: list[dict[str, Any]] | None,
    ) -> tuple[list[dict[str, Any]], int, int]:
        text = "\n".join(
            f"{message.get('role', 'user')}: {message.get('content', '')}"
            for message in messages
        )
        parts: list[dict[str, Any]] = [{"text": text}]
        total_bytes = 0
        media_items = 0

        # Preserve ContextOS evidence order. Raw bytes are bounded before base64 expansion so
        # both countTokens and generateContent stay inside predictable gateway memory bounds.
        for asset in assets or []:
            if media_items >= _MAX_MEDIA_ITEMS:
                break
            mime = str(asset.get("mime", ""))
            if not mime.startswith(("image/", "audio/", "video/")):
                continue
            try:
                path = Path(str(asset.get("path", "")))
                if not path.is_file():
                    continue
                size = path.stat().st_size
            except OSError:
                continue
            if (
                size > _MAX_SINGLE_MEDIA_BYTES
                or total_bytes + size > _MAX_TOTAL_MEDIA_BYTES
            ):
                continue
            try:
                encoded = base64.b64encode(path.read_bytes()).decode()
            except OSError:
                continue
            parts.append(
                {
                    "inline_data": {
                        "mime_type": mime,
                        "data": encoded,
                    }
                }
            )
            media_items += 1
            total_bytes += size
        return parts, media_items, total_bytes

    async def chat(
        self,
        *,
        messages,
        assets=None,
        temperature=.2,
        max_tokens=1400,
    ):
        self.reset_request_telemetry()
        if not self.info.configured:
            raise ProviderError("Gemini 未配置")

        parts, media_items, media_bytes = self._build_parts(messages, assets)
        payload = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        generation_url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self._model_id}:generateContent?key={self.api_key}"
        )
        count_url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self._model_id}:countTokens?key={self.api_key}"
        )

        try:
            async with httpx.AsyncClient(timeout=120) as client:
                if native_token_mode("gemini") == "off":
                    safe_input = None
                    limit_source = "operator-disabled"
                    model_input_limit = None
                    model_output_limit = None
                else:
                    (
                        safe_input,
                        limit_source,
                        model_input_limit,
                        model_output_limit,
                    ) = await self._resolve_native_input_limit(
                        client,
                        max_tokens=max_tokens,
                    )

                decision = decide_native_token_count(
                    "gemini",
                    messages=messages,
                    media_items=media_items,
                    safe_input_tokens=safe_input,
                    limit_source=limit_source,
                )
                self.update_request_telemetry(
                    **decision.to_telemetry(),
                    native_token_count_status="skipped",
                    native_token_count_input_tokens=None,
                    native_token_model_input_limit=model_input_limit,
                    native_token_model_output_limit=model_output_limit,
                    native_token_media_bytes=media_bytes,
                    native_token_count_endpoint="models.countTokens",
                )

                if decision.should_count:
                    try:
                        count_response = await client.post(
                            count_url,
                            json={"contents": payload["contents"]},
                            timeout=15,
                        )
                        if count_response.status_code >= 400:
                            self.update_request_telemetry(
                                native_token_count_status=(
                                    f"fallback-http-{count_response.status_code}"
                                )
                            )
                        else:
                            count_payload = count_response.json()
                            input_tokens = int(count_payload.get("totalTokens") or 0)
                            if input_tokens <= 0:
                                raise ValueError("missing-totalTokens")
                            details = count_payload.get("promptTokensDetails") or []
                            self.update_request_telemetry(
                                native_token_count_status="success",
                                native_token_count_input_tokens=input_tokens,
                                native_token_count_modality_details=details,
                            )
                            if native_count_exceeds_limit(input_tokens, decision):
                                self.update_request_telemetry(
                                    native_token_count_status="blocked-over-limit"
                                )
                                raise ProviderError(
                                    "Gemini 输入超过当前安全 token 上限："
                                    f"{input_tokens} > {decision.safe_input_tokens}"
                                )
                    except ProviderError:
                        raise
                    except (httpx.HTTPError, TypeError, ValueError):
                        # Counting is an optimization/safety escalation, not a generation
                        # dependency. Fail open to the already-bounded local ContextOS pack.
                        self.update_request_telemetry(
                            native_token_count_status="fallback-count-unavailable"
                        )

                response = await client.post(generation_url, json=payload)
        except ProviderError:
            raise
        except httpx.HTTPError as exc:
            raise ProviderError("Gemini 连接失败") from exc

        if response.status_code >= 400:
            raise ProviderError(
                f"Gemini 请求失败 {response.status_code}: {response.text[:300]}"
            )
        try:
            return "\n".join(
                item.get("text", "")
                for item in response.json()["candidates"][0]["content"]["parts"]
                if "text" in item
            )
        except Exception as exc:
            raise ProviderError("Gemini 返回格式异常") from exc
