from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
import time
from typing import Any

import httpx

from .base import BaseProvider, ProviderError, ProviderInfo
from .context_budget import load_provider_context_budget
from .native_tokens import (
    decide_native_token_count,
    native_count_exceeds_limit,
    trusted_token_count_endpoint,
)

_MAX_INLINE_MEDIA_BYTES = 24 * 1024 * 1024
_MAX_INLINE_TOTAL_BYTES = 32 * 1024 * 1024


def _data_url(path: str | Path, mime: str | None = None) -> str:
    source = Path(path)
    media_type = mime or mimetypes.guess_type(source.name)[0] or "application/octet-stream"
    encoded = base64.b64encode(source.read_bytes()).decode()
    return f"data:{media_type};base64,{encoded}"


def _inline_size(asset: dict[str, Any]) -> int | None:
    try:
        path = Path(str(asset.get("path", "")))
        if not path.is_file():
            return None
        size = path.stat().st_size
        return size if size <= _MAX_INLINE_MEDIA_BYTES else None
    except OSError:
        return None


def _count_url(base_url: str, configured: str) -> str:
    value = str(configured or "").strip()
    if value.startswith(("https://", "http://")):
        return value
    return f"{base_url.rstrip('/')}/{value.lstrip('/')}"


def _nested_positive_int(payload: Any, field: str) -> int:
    value = payload
    for part in str(field or "input_tokens").split("."):
        if not isinstance(value, dict) or part not in value:
            raise ValueError(f"missing token-count response field: {field}")
        value = value[part]
    number = int(value or 0)
    if number <= 0:
        raise ValueError(f"non-positive token-count response field: {field}")
    return number


class OpenAICompatProvider(BaseProvider):
    def __init__(
        self,
        *,
        key: str,
        name: str,
        vendor: str,
        api_key: str | None,
        base_url: str,
        model: str | None,
        multimodal: bool,
        note: str = "",
        extra_headers: dict[str, str] | None = None,
        supports_video: bool = False,
        supports_audio: bool = False,
        auth_optional: bool = False,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.extra_headers = extra_headers or {}
        configured = bool(model and (api_key or auth_optional))
        self.info = ProviderInfo(
            key=key,
            name=name,
            vendor=vendor,
            model=model,
            configured=configured,
            multimodal=multimodal,
            supports_video=supports_video,
            supports_audio=supports_audio,
            note=note,
        )

    async def chat(
        self,
        *,
        messages: list[dict[str, Any]],
        assets: list[dict[str, Any]] | None = None,
        temperature: float = .2,
        max_tokens: int = 1400,
    ) -> str:
        self.reset_request_telemetry()
        if not self.info.configured:
            raise ProviderError(f"{self.info.name} 未配置")

        out = [dict(message) for message in messages]
        assets = assets or []
        total_bytes = 0
        image_count = 0
        video_count = 0
        audio_count = 0

        if out and out[-1].get("role") == "user" and assets:
            content: list[dict[str, Any]] = [
                {"type": "text", "text": str(out[-1].get("content", ""))}
            ]

            # Preserve upstream evidence priority instead of regrouping by modality.
            # The ContextOS pack puts exact temporal evidence and high-value derivatives
            # first. A global raw-byte budget bounds base64 expansion, gateway pressure and
            # request memory even when several individually valid media files are present.
            for asset in assets:
                mime = str(asset.get("mime", ""))
                size = _inline_size(asset)
                if size is None or total_bytes + size > _MAX_INLINE_TOTAL_BYTES:
                    continue

                part: dict[str, Any] | None = None
                if mime.startswith("image/") and self.info.multimodal and image_count < 10:
                    part = {
                        "type": "image_url",
                        "image_url": {"url": _data_url(asset["path"], mime)},
                    }
                    image_count += 1
                elif mime.startswith("video/") and self.info.supports_video and video_count < 2:
                    part = {
                        "type": "video_url",
                        "video_url": {"url": _data_url(asset["path"], mime)},
                        "fps": 1,
                    }
                    video_count += 1
                elif mime.startswith("audio/") and self.info.supports_audio and audio_count < 3:
                    part = {
                        "type": "audio_url",
                        "audio_url": {"url": _data_url(asset["path"], mime)},
                    }
                    audio_count += 1

                if part is not None:
                    content.append(part)
                    total_bytes += size

            if len(content) > 1:
                out[-1] = {"role": "user", "content": content}

        headers = {"Content-Type": "application/json", **self.extra_headers}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = {
            "model": self.model,
            "messages": out,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        profile = load_provider_context_budget(self.info.key, model=self.model)
        if profile.context_window_tokens:
            safe_input_tokens = max(
                1,
                int(profile.context_window_tokens)
                - max(int(profile.output_reserve_tokens), int(max_tokens)),
            )
            limit_source = "operator-context-profile"
        else:
            safe_input_tokens = None
            limit_source = None
        endpoint = trusted_token_count_endpoint(self.info.key)
        media_items = image_count + video_count + audio_count
        decision = decide_native_token_count(
            self.info.key,
            messages=messages,
            media_items=media_items,
            safe_input_tokens=safe_input_tokens,
            limit_source=limit_source,
        )
        endpoint_status = (
            "trusted-configured"
            if endpoint.enabled
            else "configured-but-untrusted"
            if endpoint.url
            else "not-configured"
        )
        self.update_request_telemetry(
            **decision.to_telemetry(),
            native_token_count_status="skipped",
            native_token_count_input_tokens=None,
            native_token_count_endpoint=(endpoint.url if endpoint.enabled else None),
            native_token_count_endpoint_status=endpoint_status,
            native_token_count_response_field=endpoint.response_field,
            native_token_media_bytes=total_bytes,
            native_token_count_extra_rtt_ms=None,
            native_token_estimate_delta=None,
            native_token_exact_estimate_ratio=None,
        )

        try:
            async with httpx.AsyncClient(timeout=90) as client:
                if decision.should_count and endpoint.enabled and endpoint.url:
                    started = time.perf_counter()
                    try:
                        count_response = await client.post(
                            _count_url(self.base_url, endpoint.url),
                            headers=headers,
                            json={"model": self.model, "messages": out},
                            timeout=15,
                        )
                        rtt_ms = (time.perf_counter() - started) * 1000.0
                        self.update_request_telemetry(
                            native_token_count_extra_rtt_ms=round(rtt_ms, 3)
                        )
                        if count_response.status_code >= 400:
                            self.update_request_telemetry(
                                native_token_count_status=(
                                    f"fallback-http-{count_response.status_code}"
                                )
                            )
                        else:
                            input_tokens = _nested_positive_int(
                                count_response.json(), endpoint.response_field
                            )
                            estimated = max(1, int(decision.estimated_text_tokens))
                            self.update_request_telemetry(
                                native_token_count_status="success",
                                native_token_count_input_tokens=input_tokens,
                                native_token_estimate_delta=input_tokens - estimated,
                                native_token_exact_estimate_ratio=round(
                                    input_tokens / estimated, 6
                                ),
                            )
                            if native_count_exceeds_limit(input_tokens, decision):
                                self.update_request_telemetry(
                                    native_token_count_status="blocked-over-limit"
                                )
                                raise ProviderError(
                                    f"{self.info.name} 输入超过当前安全 token 上限："
                                    f"{input_tokens} > {decision.safe_input_tokens}"
                                )
                    except ProviderError:
                        raise
                    except (httpx.HTTPError, TypeError, ValueError):
                        # The endpoint is operator-declared rather than a universal OpenAI-
                        # compatible contract. If it breaks, fail open to the already bounded
                        # ContextOS request and make the calibration failure visible.
                        self.update_request_telemetry(
                            native_token_count_status="fallback-count-unavailable"
                        )
                elif decision.should_count and not endpoint.enabled:
                    self.update_request_telemetry(
                        native_token_count_status="skipped-no-trusted-endpoint"
                    )

                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                )
        except ProviderError:
            raise
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.info.name} 连接失败") from exc

        if response.status_code >= 400:
            raise ProviderError(
                f"{self.info.name} 请求失败 {response.status_code}: "
                f"{response.text[:300]}"
            )
        try:
            return response.json()["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderError(f"{self.info.name} 返回格式异常") from exc
