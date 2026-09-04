from __future__ import annotations

import base64
from pathlib import Path

import httpx

from .base import BaseProvider, ProviderError, ProviderInfo

_MAX_MEDIA_ITEMS = 6
_MAX_SINGLE_MEDIA_BYTES = 18 * 1024 * 1024
_MAX_TOTAL_MEDIA_BYTES = 32 * 1024 * 1024


class GeminiProvider(BaseProvider):
    def __init__(self, api_key, model):
        self.api_key, self.model = api_key, model
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

    async def chat(
        self,
        *,
        messages,
        assets=None,
        temperature=.2,
        max_tokens=1400,
    ):
        if not self.info.configured:
            raise ProviderError("Gemini 未配置")

        text = "\n".join(
            f"{message.get('role', 'user')}: {message.get('content', '')}"
            for message in messages
        )
        parts = [{"text": text}]
        total_bytes = 0
        media_items = 0

        # Preserve ContextOS evidence order and enforce a global byte budget. Base64 adds
        # roughly one third overhead, so bounding raw bytes is critical for predictable
        # gateway memory and TTFT on mixed image/video/audio tasks.
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

        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent?key={self.api_key}"
        )
        payload = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                response = await client.post(url, json=payload)
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
