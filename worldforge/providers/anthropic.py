from __future__ import annotations

import base64
from pathlib import Path
import time
from typing import Any
from urllib.parse import quote

import httpx

from .base import BaseProvider, ProviderError, ProviderInfo
from .context_budget import load_provider_context_budget
from .native_tokens import (
    decide_native_token_count,
    native_count_exceeds_limit,
    native_token_mode,
)

# Direct Claude API accepts 10 MB per base64 image and 32 MB total request bodies. Keep raw
# media below those wire limits after ~4/3 base64 expansion, with room for JSON/text overhead.
_MAX_IMAGES = 6
_MAX_SINGLE_IMAGE_BYTES = 7 * 1024 * 1024
_MAX_TOTAL_IMAGE_BYTES = 20 * 1024 * 1024
_SUPPORTED_IMAGE_MIMES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
_MODEL_LIMIT_CACHE_SECONDS = 3600.0
_MODEL_LIMIT_FAILURE_CACHE_SECONDS = 60.0


class AnthropicProvider(BaseProvider):
    def __init__(self, api_key, model):
        self.api_key, self.model = api_key, model
        self._native_limits_cache: tuple[float, int | None, int | None, str] | None = None
        self.info = ProviderInfo(
            "anthropic",
            "Claude",
            "Anthropic",
            model,
            bool(api_key and model),
            True,
            note="适合长文档、图片理解与复杂分析",
        )

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": str(self.api_key or ""),
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

    async def _resolve_native_input_limit(
        self,
        client: httpx.AsyncClient,
        *,
        max_tokens: int,
    ) -> tuple[int | None, str | None, int | None, int | None]:
        profile = load_provider_context_budget("anthropic", model=self.model)
        if profile.context_window_tokens:
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

        model_id = quote(str(self.model or ""), safe="")
        try:
            response = await client.get(
                f"https://api.anthropic.com/v1/models/{model_id}",
                headers=self._headers(),
                timeout=5,
            )
            if response.status_code >= 400:
                raise ValueError(f"http-{response.status_code}")
            payload = response.json()
            input_limit = int(payload.get("max_input_tokens") or 0) or None
            output_limit = int(payload.get("max_tokens") or 0) or None
            if input_limit is None:
                raise ValueError("missing-max_input_tokens")
        except (httpx.HTTPError, TypeError, ValueError):
            self._native_limits_cache = (
                now + _MODEL_LIMIT_FAILURE_CACHE_SECONDS,
                None,
                None,
                "anthropic-model-metadata-unavailable",
            )
            return None, "anthropic-model-metadata-unavailable", None, None

        self._native_limits_cache = (
            now + _MODEL_LIMIT_CACHE_SECONDS,
            input_limit,
            output_limit,
            "anthropic-models.get",
        )
        return input_limit, "anthropic-models.get", input_limit, output_limit

    def _build_messages(
        self,
        messages: list[dict[str, Any]],
        assets: list[dict[str, Any]] | None,
    ) -> tuple[str, list[dict[str, Any]], int, int]:
        system = ""
        msgs: list[dict[str, Any]] = []
        for message in messages:
            if message.get("role") == "system":
                system += str(message.get("content", "")) + "\n"
            else:
                msgs.append(
                    {
                        "role": message.get("role", "user"),
                        "content": str(message.get("content", "")),
                    }
                )

        total_bytes = 0
        media_items = 0
        if msgs and msgs[-1]["role"] == "user":
            blocks: list[dict[str, Any]] = [
                {"type": "text", "text": str(msgs[-1]["content"])}
            ]
            for asset in assets or []:
                if media_items >= _MAX_IMAGES:
                    break
                mime = str(asset.get("mime", ""))
                if mime not in _SUPPORTED_IMAGE_MIMES:
                    continue
                try:
                    path = Path(str(asset.get("path", "")))
                    if not path.is_file():
                        continue
                    size = path.stat().st_size
                except OSError:
                    continue
                if (
                    size > _MAX_SINGLE_IMAGE_BYTES
                    or total_bytes + size > _MAX_TOTAL_IMAGE_BYTES
                ):
                    continue
                try:
                    encoded = base64.b64encode(path.read_bytes()).decode()
                except OSError:
                    continue
                blocks.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": mime,
                            "data": encoded,
                        },
                    }
                )
                media_items += 1
                total_bytes += size
            if media_items:
                msgs[-1] = {"role": "user", "content": blocks}
        return system.strip(), msgs, media_items, total_bytes

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
            raise ProviderError("Claude 未配置")

        system, msgs, media_items, media_bytes = self._build_messages(messages, assets)
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": msgs,
        }
        if system:
            payload["system"] = system
        count_payload: dict[str, Any] = {
            "model": self.model,
            "messages": msgs,
        }
        if system:
            count_payload["system"] = system

        try:
            async with httpx.AsyncClient(timeout=90) as client:
                if native_token_mode("anthropic") == "off":
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
                    "anthropic",
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
                    native_token_count_endpoint="/v1/messages/count_tokens",
                )

                if decision.should_count:
                    try:
                        count_response = await client.post(
                            "https://api.anthropic.com/v1/messages/count_tokens",
                            headers=self._headers(),
                            json=count_payload,
                            timeout=15,
                        )
                        if count_response.status_code >= 400:
                            self.update_request_telemetry(
                                native_token_count_status=(
                                    f"fallback-http-{count_response.status_code}"
                                )
                            )
                        else:
                            input_tokens = int(
                                count_response.json().get("input_tokens") or 0
                            )
                            if input_tokens <= 0:
                                raise ValueError("missing-input_tokens")
                            self.update_request_telemetry(
                                native_token_count_status="success",
                                native_token_count_input_tokens=input_tokens,
                            )
                            if native_count_exceeds_limit(input_tokens, decision):
                                self.update_request_telemetry(
                                    native_token_count_status="blocked-over-limit"
                                )
                                raise ProviderError(
                                    "Claude 输入超过当前安全 token 上限："
                                    f"{input_tokens} > {decision.safe_input_tokens}"
                                )
                    except ProviderError:
                        raise
                    except (httpx.HTTPError, TypeError, ValueError):
                        self.update_request_telemetry(
                            native_token_count_status="fallback-count-unavailable"
                        )

                response = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers=self._headers(),
                    json=payload,
                )
        except ProviderError:
            raise
        except httpx.HTTPError as exc:
            raise ProviderError("Claude 连接失败") from exc

        if response.status_code >= 400:
            raise ProviderError(
                f"Claude 请求失败 {response.status_code}: {response.text[:300]}"
            )
        try:
            return "\n".join(
                item.get("text", "")
                for item in response.json().get("content", [])
                if item.get("type") == "text"
            )
        except (TypeError, ValueError) as exc:
            raise ProviderError("Claude 返回格式异常") from exc
