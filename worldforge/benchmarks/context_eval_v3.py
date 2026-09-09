from __future__ import annotations

from dataclasses import asdict, dataclass
import os
from typing import Any
from unittest.mock import patch

from worldforge.context.token_budget import ProviderAwareContextBudgetBroker

from .context_eval_v2 import run_context_benchmark as _run_context_benchmark_v2


@dataclass(frozen=True)
class ContextBenchmarkResult:
    constraint_recall: float
    long_range_identifier_recall: float
    context_compression_ratio: float
    semantic_routing_accuracy: float
    semantic_call_rate: float
    max_temporal_frame_budget: int
    current_state_tracking: float
    open_question_closure: float
    premise_awareness: float
    incremental_state_cache: float
    long_horizon_turns: int
    provider_token_budget_safety: float
    passed: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _conversation(index: int) -> dict[str, Any]:
    return {
        "id": f"token-bench-{index}",
        "role": "user" if index % 2 == 0 else "assistant",
        "content": (
            f"第 {index} 轮 release 调试：render_fence_{index}=0x{index:04x}; "
            + "护盾状态与资源时序保持观察。" * 45
        ),
        "payload": {},
    }


def _derived(kind: str, marker: str, fill: str) -> dict[str, Any]:
    return {
        "id": f"context:{kind}",
        "role": "user",
        "content": marker + "\n" + (fill * 900),
        "payload": {"system_derived": True, "context_kind": kind},
    }


def _provider_token_budget_safety() -> float:
    rows = [
        *[_conversation(index) for index in range(8)],
        _derived("verification", "verification-token-marker", "验证证据"),
        _derived("task_state", "task-state-token-marker", "当前状态"),
        _derived("project_memory", "project-memory-token-marker", "历史记忆"),
        _derived("evidence_control", "evidence-control-token-marker", "证据控制"),
    ]
    env = {
        "LINGJING_OPENAI_CONTEXT_WINDOW_TOKENS": "4096",
        "LINGJING_OPENAI_HISTORY_BUDGET_TOKENS": "900",
        "LINGJING_OPENAI_OUTPUT_RESERVE_TOKENS": "1400",
        "LINGJING_OPENAI_KERNEL_BUDGET_TOKENS": "430",
        "LINGJING_OPENAI_PER_MESSAGE_BUDGET_TOKENS": "180",
    }
    with patch.dict(os.environ, env, clear=False):
        broker = ProviderAwareContextBudgetBroker(
            max_history_messages=8,
            history_char_budget=15000,
            kernel_char_budget=5800,
            per_message_char_budget=5800,
        )
        token = broker.bind_provider("openai", model="benchmark-model")
        try:
            packed = broker.pack(rows)
        finally:
            broker.reset_provider(token)

    text = "\n".join(str(message.get("content", "")) for message in packed.messages)
    telemetry = packed.telemetry
    return float(
        telemetry.get("context_budget_token_mode") == "provider-profile-enforced"
        and telemetry.get("context_budget_token_fallback") is False
        and telemetry.get("context_budget_history_token_limit") == 900
        and telemetry.get("context_budget_output_estimated_tokens", 999999) <= 900
        and telemetry.get("context_budget_token_limit_safe") is True
        and telemetry.get("context_budget_provider_last_n_safe") is True
        and "verification-token-marker" in text
        and "task-state-token-marker" in text
    )


def run_context_benchmark() -> ContextBenchmarkResult:
    base = _run_context_benchmark_v2()
    token_safety = _provider_token_budget_safety()
    passed = bool(base.passed and token_safety == 1.0)
    return ContextBenchmarkResult(
        constraint_recall=base.constraint_recall,
        long_range_identifier_recall=base.long_range_identifier_recall,
        context_compression_ratio=base.context_compression_ratio,
        semantic_routing_accuracy=base.semantic_routing_accuracy,
        semantic_call_rate=base.semantic_call_rate,
        max_temporal_frame_budget=base.max_temporal_frame_budget,
        current_state_tracking=base.current_state_tracking,
        open_question_closure=base.open_question_closure,
        premise_awareness=base.premise_awareness,
        incremental_state_cache=base.incremental_state_cache,
        long_horizon_turns=base.long_horizon_turns,
        provider_token_budget_safety=token_safety,
        passed=passed,
    )
