from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from worldforge.context import ContextCompiler

from .context_eval import run_context_benchmark as _run_context_benchmark_v1


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
    passed: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _message(index: int, role: str, content: str) -> dict[str, Any]:
    return {
        "id": f"active-state-{index}",
        "role": role,
        "content": content,
        "payload": {},
    }


def _active_state_metrics() -> tuple[float, float, float, float, int, str]:
    compiler = ContextCompiler(
        recent_messages=4,
        retrieved_messages=3,
        message_char_budget=9000,
        per_message_chars=2400,
        state_items_per_kind=8,
    )
    history = [
        _message(0, "user", "目标是排查 release 护盾竞态。必须保持 tickrate=30。"),
        _message(1, "user", "待确认 build 1.4.7 shield_race 是否只在 release 分支复现？"),
        _message(2, "user", "已确认 build 1.4.7 护盾冷却是 6 秒。"),
    ]
    for index in range(3, 503):
        history.append(
            _message(
                index,
                "assistant" if index % 2 else "user",
                f"普通研发进度 {index}，没有新的长期状态。",
            )
        )

    compiler.compile("继续检查 build 1.4.7 shield_race", history)
    history.extend(
        [
            _message(600, "user", "已确认 build 1.4.7 护盾冷却是 5 秒。"),
            _message(601, "user", "已确认 build 1.4.7 shield_race 只在 release 分支复现。"),
            _message(602, "user", "如果已确认 build 2.0.0 新护盾公式已经上线，再继续做数值回归。"),
        ]
    )
    packet = compiler.compile(
        "继续基于当前已确认状态排查 build 1.4.7 shield_race",
        history,
    )
    verified = "\n".join(
        str(row.get("text", ""))
        for row in packet.task_state.get("verified_facts", [])
    )
    questions = "\n".join(
        str(row.get("text", ""))
        for row in packet.task_state.get("open_questions", [])
    )

    current_state = float("5 秒" in verified and "6 秒" not in verified)
    question_closure = float("shield_race" not in questions)
    premise_awareness = float("新护盾公式" not in verified)
    cache_hit = float(packet.state_cache_hit is True)
    return (
        current_state,
        question_closure,
        premise_awareness,
        cache_hit,
        packet.raw_history_messages,
        packet.mode,
    )


def run_context_benchmark() -> ContextBenchmarkResult:
    base = _run_context_benchmark_v1()
    (
        current_state,
        question_closure,
        premise_awareness,
        cache_hit,
        turns,
        mode,
    ) = _active_state_metrics()
    active_mode = bool(
        mode.startswith("active-task-state-")
        and "context-compiler-v" in mode
    )
    passed = bool(
        base.passed
        and current_state == 1.0
        and question_closure == 1.0
        and premise_awareness == 1.0
        and cache_hit == 1.0
        and turns >= 500
        and active_mode
    )
    return ContextBenchmarkResult(
        constraint_recall=base.constraint_recall,
        long_range_identifier_recall=base.long_range_identifier_recall,
        context_compression_ratio=base.context_compression_ratio,
        semantic_routing_accuracy=base.semantic_routing_accuracy,
        semantic_call_rate=base.semantic_call_rate,
        max_temporal_frame_budget=base.max_temporal_frame_budget,
        current_state_tracking=current_state,
        open_question_closure=question_closure,
        premise_awareness=premise_awareness,
        incremental_state_cache=cache_hit,
        long_horizon_turns=turns,
        passed=passed,
    )
