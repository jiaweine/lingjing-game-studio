from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any

import httpx

from .base import BaseProvider, ProviderError, ProviderInfo


def _data_url(path: str | Path, mime: str | None = None) -> str:
    source = Path(path)
    media_type = mime or mimetypes.guess_type(source.name)[0] or "application/octet-stream"
    encoded = base64.b64encode(source.read_bytes()).decode()
    return f"data:{media_type};base64,{encoded}"


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
        if not self.info.configured:
            raise ProviderError(f"{self.info.name} 未配置")

        out = [dict(message) for message in messages]
        assets = assets or []
        images = [
            asset for asset in assets
            if str(asset.get("mime", "")).startswith("image/")
        ]
        audio = [
            asset for asset in assets
            if str(asset.get("mime", "")).startswith("audio/")
        ]

        if out and out[-1].get("role") == "user" and (
            (images and self.info.multimodal)
            or (audio and self.info.supports_audio)
        ):
            content: list[dict[str, Any]] = [
                {"type": "text", "text": str(out[-1].get("content", ""))}
            ]
            if self.info.multimodal:
                for asset in images[:10]:
                    content.append({
                        "type": "image_url",
                        "image_url": {
                            "url": _data_url(asset["path"], asset.get("mime"))
                        },
                    })
            if self.info.supports_audio:
                for asset in audio[:3]:
                    content.append({
                        "type": "audio_url",
                        "audio_url": {
                            "url": _data_url(asset["path"], asset.get("mime"))
                        },
                    })
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
        try:
            async with httpx.AsyncClient(timeout=90) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                )
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
