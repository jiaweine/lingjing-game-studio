from __future__ import annotations

import pytest

from worldforge.product import ProductAnalyzer


class _CapturingProvider:
    def __init__(self):
        self.assets = None
        self.messages = None

    async def chat(self, *, messages, assets=None, **kwargs):
        self.messages = list(messages)
        self.assets = list(assets or [])
        return "ok"


class _Providers:
    def __init__(self, provider=None):
        self.provider = provider

    def choose(self, *_args, **_kwargs):
        return self.provider


def test_system_derived_context_never_poison_intent_router():
    analyzer = ProductAnalyzer(object(), _Providers())
    history = [
        {
            "id": "context:verification-contract",
            "role": "user",
            "content": "版本 回归 bug 异常 复现 synthetic scenario",
            "payload": {"system_derived": True},
        }
    ]
    assets = [
        {
            "id": "log",
            "name": "runtime.log",
            "mime": "text/plain",
            "meta": {"kind": "text"},
        }
    ]

    assert analyzer.intent("请分析 marker_zeta 的上下文", assets, history) == "general"


def test_metadata_only_video_turn_sends_zero_provider_media():
    analyzer = ProductAnalyzer(object(), _Providers())
    assets = [
        {
            "id": "run",
            "name": "build-1.4.7.mp4",
            "mime": "video/mp4",
            "path": "/tmp/not-opened.mp4",
            "meta": {
                "kind": "video",
                "keyframes": ["/tmp/not-opened-frame.jpg"],
                "_context": {
                    "kind": "video",
                    "selected": True,
                    "rank": 1,
                    "provider_visual_enabled": False,
                    "provider_audio_enabled": False,
                    "evidence_temporal_frame_budget": 0,
                },
            },
        }
    ]

    assert analyzer._model_assets(assets) == []


@pytest.mark.asyncio
async def test_implicit_screenshot_question_still_sends_visual_evidence():
    provider = _CapturingProvider()
    analyzer = ProductAnalyzer(object(), _Providers(provider))
    asset = {
        "id": "shot",
        "name": "boss-state.png",
        "mime": "image/png",
        "path": "/tmp/boss-state.png",
        "meta": {"kind": "image", "width": 1280, "height": 720},
    }

    async def sink(_type, _payload):
        return None

    result = await analyzer.run(
        text="Boss 现在是什么状态？",
        assets=[asset],
        provider_key="auto",
        sink=sink,
        history=[],
        human_feedback_gate=False,
    )

    assert result["context"]["evidence_needs_visual"] is True
    assert provider.assets is not None
    assert len(provider.assets) == 1
    assert provider.assets[0]["id"] == "shot"
