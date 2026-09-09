from __future__ import annotations

import os
from unittest.mock import patch

from worldforge.context.token_budget import ProviderAwareContextBudgetBroker
from worldforge.providers.context_budget import (
    estimate_text_tokens,
    load_provider_context_budget,
)


def _conversation(index: int, content: str) -> dict:
    return {
        "id": f"provider-budget-{index}",
        "role": "user" if index % 2 == 0 else "assistant",
        "content": content,
        "payload": {},
    }


def _derived(kind: str, content: str) -> dict:
    return {
        "id": f"context:{kind}",
        "role": "user",
        "content": content,
        "payload": {"system_derived": True, "context_kind": kind},
    }


def test_profile_derives_history_share_from_declared_context_window_without_model_table():
    with patch.dict(
        os.environ,
        {
            "LINGJING_QWEN_CONTEXT_WINDOW_TOKENS": "10000",
            "LINGJING_QWEN_OUTPUT_RESERVE_TOKENS": "2000",
        },
        clear=False,
    ):
        profile = load_provider_context_budget("qwen", model="private-qwen-deployment")

    assert profile.context_window_tokens == 10000
    assert profile.output_reserve_tokens == 2000
    assert profile.history_budget_tokens == 2800
    assert profile.enabled is True
    assert "derived-history-share" in profile.source


def test_unconfigured_provider_keeps_character_fallback_and_reports_it():
    keys = [
        "LINGJING_CUSTOM_CONTEXT_WINDOW_TOKENS",
        "LINGJING_CUSTOM_HISTORY_BUDGET_TOKENS",
        "LINGJING_CUSTOM_OUTPUT_RESERVE_TOKENS",
    ]
    with patch.dict(os.environ, {}, clear=False):
        saved = {key: os.environ.pop(key, None) for key in keys}
        try:
            broker = ProviderAwareContextBudgetBroker()
            token = broker.bind_provider("custom", model="unknown-private-model")
            try:
                packed = broker.pack(
                    [_conversation(0, "普通历史 " + ("上下文" * 1000))]
                )
            finally:
                broker.reset_provider(token)
        finally:
            for key, value in saved.items():
                if value is not None:
                    os.environ[key] = value

    assert packed.telemetry["context_budget_token_fallback"] is True
    assert packed.telemetry["context_budget_unit"] == "chars"
    assert packed.telemetry["context_budget_requested_provider_key"] == "custom"
    assert packed.telemetry["context_budget_token_limit_safe"] is None


def test_mixed_cjk_and_code_history_is_trimmed_to_explicit_provider_token_budget():
    env = {
        "LINGJING_OPENAI_HISTORY_BUDGET_TOKENS": "760",
        "LINGJING_OPENAI_KERNEL_BUDGET_TOKENS": "360",
        "LINGJING_OPENAI_PER_MESSAGE_BUDGET_TOKENS": "170",
    }
    rows = [
        *[
            _conversation(
                index,
                f"build 1.4.{index} render_fence_{index}=0x{index:04x}; "
                + ("护盾资源竞态与帧同步继续观察。" * 50),
            )
            for index in range(8)
        ],
        _derived(
            "verification",
            "verification-must-survive\n" + ("当前证据优先。" * 800),
        ),
        _derived(
            "task_state",
            "task-state-must-survive\n" + ("必须保持 tickrate=30。" * 500),
        ),
        _derived(
            "project_memory",
            "project-memory-lower-priority\n" + ("旧项目事实。" * 800),
        ),
    ]

    with patch.dict(os.environ, env, clear=False):
        broker = ProviderAwareContextBudgetBroker()
        token = broker.bind_provider("openai", model="configured-model")
        try:
            packed = broker.pack(rows)
        finally:
            broker.reset_provider(token)

    telemetry = packed.telemetry
    text = "\n".join(message["content"] for message in packed.messages)
    assert telemetry["context_budget_token_fallback"] is False
    assert telemetry["context_budget_output_estimated_tokens"] <= 760
    assert telemetry["context_budget_token_limit_safe"] is True
    assert len(packed.messages) <= 8
    assert "verification-must-survive" in text
    assert "task-state-must-survive" in text


def test_auto_budget_is_explicit_common_denominator_not_guessed_from_vendor_models():
    env = {
        "LINGJING_AUTO_HISTORY_BUDGET_TOKENS": "640",
        "LINGJING_AUTO_OUTPUT_RESERVE_TOKENS": "1600",
    }
    with patch.dict(os.environ, env, clear=False):
        broker = ProviderAwareContextBudgetBroker()
        token = broker.bind_provider("auto")
        try:
            packed = broker.pack(
                [
                    _conversation(i, "自动路由上下文 " + ("混合 token abc_123/路径 " * 90))
                    for i in range(8)
                ]
            )
        finally:
            broker.reset_provider(token)

    assert packed.telemetry["context_budget_requested_provider_key"] == "auto"
    assert packed.telemetry["context_budget_history_token_limit"] == 640
    assert packed.telemetry["context_budget_token_fallback"] is False
    assert packed.telemetry["context_budget_output_estimated_tokens"] <= 640


def test_multilingual_estimator_charges_cjk_and_code_more_than_plain_ascii_words():
    plain = estimate_text_tokens("simple english words with ordinary spacing")
    mixed = estimate_text_tokens("护盾冷却 build_1.4.7 render/fence=0xFF 必须保持 30fps")

    assert plain > 0
    assert mixed > plain
