from __future__ import annotations

from dataclasses import dataclass
import os
from unittest.mock import patch

import pytest

from worldforge.product import ProductAnalyzer
from worldforge.providers.context_budget import estimate_text_tokens


@dataclass
class _Info:
    key: str = "openai"
    model: str = "integration-model"
    configured: bool = True
    multimodal: bool = True
    supports_video: bool = False
    supports_audio: bool = False


class _Provider:
    def __init__(self) -> None:
        self.info = _Info()
        self.messages = None

    async def chat(self, *, messages, assets=None, **kwargs):
        self.messages = list(messages)
        return "provider-token-budget-ok"


class _Providers:
    def __init__(self, provider: _Provider) -> None:
        self.provider = provider
        self.providers = {"openai": provider}

    def choose(self, preferred, assets):
        return self.provider if preferred in {"openai", "auto"} else None


def _message(index: int) -> dict:
    return {
        "id": f"provider-int-{index}",
        "role": "user" if index % 2 == 0 else "assistant",
        "content": (
            f"第 {index} 轮 build 1.4.7 render_fence_{index}=0x{index:04x}; "
            + ("护盾资源窗口继续观察。" * 70)
        ),
        "payload": {},
    }


@pytest.mark.asyncio
async def test_product_analyzer_uses_request_scoped_provider_token_profile():
    provider = _Provider()
    analyzer = ProductAnalyzer(object(), _Providers(provider))
    history = [
        {
            "id": "provider-int-goal",
            "role": "user",
            "content": "目标是排查 release 卡顿。必须保持 tickrate=30。",
            "payload": {},
        },
        *[_message(index) for index in range(1, 40)],
    ]

    async def sink(_event_type, _payload):
        return None

    env = {
        "LINGJING_OPENAI_HISTORY_BUDGET_TOKENS": "680",
        "LINGJING_OPENAI_KERNEL_BUDGET_TOKENS": "300",
        "LINGJING_OPENAI_PER_MESSAGE_BUDGET_TOKENS": "150",
    }
    with patch.dict(os.environ, env, clear=False):
        result = await analyzer.run(
            text="继续按当前约束检查 release 卡顿",
            assets=[],
            provider_key="openai",
            sink=sink,
            history=history,
            human_feedback_gate=False,
        )

    context = result["context"]
    assert result["answer"] == "provider-token-budget-ok"
    assert context["context_budget_requested_provider_key"] == "openai"
    assert context["context_budget_requested_provider_model"] == "integration-model"
    assert context["context_budget_history_token_limit"] == 680
    assert context["context_budget_token_fallback"] is False
    assert context["context_budget_token_limit_safe"] is True
    assert context["context_budget_output_estimated_tokens"] <= 680

    assert provider.messages is not None
    prior = provider.messages[1:-1]
    assert len(prior) <= 8
    prior_tokens = sum(
        estimate_text_tokens(str(message.get("content", ""))) + 4
        for message in prior
    )
    assert prior_tokens <= 680
    assert any("tickrate=30" in str(message.get("content", "")) for message in prior)
