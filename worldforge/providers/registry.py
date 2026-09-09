from __future__ import annotations

import os

from .anthropic import AnthropicProvider
from .base import ProviderInfo
from .gemini import GeminiProvider
from .openai_compat import OpenAICompatProvider


class ProviderRegistry:
    """Inference capability registry.

    Providers are replaceable perception/reasoning resources. WorldForge remains the
    owner of planning, tool permissions, execution, rollback and completion decisions.
    """

    def __init__(self) -> None:
        self.providers = {}
        self._load()

    def _load(self) -> None:
        env = os.environ
        self.providers = {
            "local_omni": OpenAICompatProvider(
                key="local_omni",
                name="本地全模态",
                vendor="Local OSS",
                api_key=env.get("LOCAL_OMNI_API_KEY"),
                base_url=env.get("LOCAL_OMNI_BASE_URL", "http://127.0.0.1:8901/v1"),
                model=env.get(
                    "LOCAL_OMNI_MODEL",
                    "Qwen/Qwen3-Omni-30B-A3B-Instruct",
                ) if env.get("LOCAL_OMNI_BASE_URL") else None,
                multimodal=True,
                supports_video=True,
                supports_audio=True,
                auth_optional=True,
                note="本地开源全模态理解，可处理文本、图像、音频与视频上下文",
            ),
            "openai": OpenAICompatProvider(
                key="openai",
                name="OpenAI",
                vendor="OpenAI",
                api_key=env.get("OPENAI_API_KEY"),
                base_url=env.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                model=env.get("OPENAI_MODEL"),
                multimodal=True,
                note="通用分析、图片理解与工具任务",
            ),
            "deepseek": OpenAICompatProvider(
                key="deepseek",
                name="DeepSeek",
                vendor="DeepSeek",
                api_key=env.get("DEEPSEEK_API_KEY"),
                base_url=env.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                model=env.get("DEEPSEEK_MODEL"),
                multimodal=False,
                note="文本推理与长上下文分析",
            ),
            "qwen": OpenAICompatProvider(
                key="qwen",
                name="通义千问",
                vendor="阿里云百炼",
                api_key=env.get("DASHSCOPE_API_KEY"),
                base_url=env.get(
                    "QWEN_BASE_URL",
                    "https://dashscope.aliyuncs.com/compatible-mode/v1",
                ),
                model=env.get("QWEN_MODEL", "qwen3-vl-plus"),
                multimodal=True,
                supports_video=True,
                note="中文、多模态、视频与文档理解",
            ),
            "doubao": OpenAICompatProvider(
                key="doubao",
                name="豆包",
                vendor="火山方舟",
                api_key=env.get("ARK_API_KEY"),
                base_url=env.get(
                    "DOUBAO_BASE_URL",
                    "https://ark.cn-beijing.volces.com/api/v3",
                ),
                model=env.get("DOUBAO_MODEL"),
                multimodal=True,
                note="中文、多模态和游戏内容场景",
            ),
            "anthropic": AnthropicProvider(
                env.get("ANTHROPIC_API_KEY"), env.get("ANTHROPIC_MODEL")
            ),
            "gemini": GeminiProvider(
                env.get("GEMINI_API_KEY"), env.get("GEMINI_MODEL")
            ),
        }
        if env.get("CUSTOM_BASE_URL"):
            self.providers["custom"] = OpenAICompatProvider(
                key="custom",
                name="自定义模型",
                vendor="OpenAI-Compatible",
                api_key=env.get("CUSTOM_API_KEY"),
                base_url=env["CUSTOM_BASE_URL"],
                model=env.get("CUSTOM_MODEL"),
                multimodal=env.get("CUSTOM_MULTIMODAL", "1") != "0",
                supports_video=env.get("CUSTOM_VIDEO", "0") == "1",
                supports_audio=env.get("CUSTOM_AUDIO", "0") == "1",
                auth_optional=env.get("CUSTOM_AUTH_OPTIONAL", "0") == "1",
                note="企业自建或私有化模型服务",
            )

    def list(self) -> list[dict]:
        rows = [provider.info.dict() for provider in self.providers.values()]
        rows.insert(
            0,
            ProviderInfo(
                "auto",
                "自动选择",
                "系统",
                None,
                True,
                True,
                True,
                True,
                "根据素材能力与可用服务自动选择",
            ).dict(),
        )
        rows.append(
            ProviderInfo(
                "demo",
                "内置演示",
                "本地",
                "Demo Engine",
                True,
                True,
                True,
                True,
                "无需密钥，用于完整体验与验收",
            ).dict()
        )
        return rows

    @staticmethod
    def _compatible(provider, assets: list[dict]) -> bool:
        if not provider.info.configured:
            return False
        needs_image = any(
            str(asset.get("mime", "")).startswith("image/") for asset in assets
        )
        needs_audio = any(
            str(asset.get("mime", "")).startswith("audio/") for asset in assets
        )
        needs_video = any(
            str(asset.get("mime", "")).startswith("video/") for asset in assets
        )
        if needs_image and not provider.info.multimodal:
            return False
        if needs_audio and not provider.info.supports_audio:
            return False
        if needs_video and not provider.info.supports_video:
            return False
        return True

    def choose(self, preferred: str | None, assets: list[dict]):
        if preferred and preferred not in {"auto", "demo"}:
            provider = self.providers.get(preferred)
            return provider if provider and self._compatible(provider, assets) else None

        has_media = any(
            str(asset.get("mime", "")).startswith(("image/", "video/", "audio/"))
            for asset in assets
        )
        order = (
            ["local_omni", "qwen", "doubao", "gemini", "openai", "anthropic", "deepseek"]
            if has_media
            else ["deepseek", "local_omni", "qwen", "doubao", "openai", "anthropic", "gemini"]
        )
        for key in order:
            provider = self.providers.get(key)
            if provider and self._compatible(provider, assets):
                return provider
        return None
