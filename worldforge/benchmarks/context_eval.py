from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from worldforge.context.compiler import ContextCompiler
from worldforge.context.evidence_controller import EvidenceController


@dataclass(frozen=True)
class ContextBenchmarkResult:
    constraint_recall: float
    long_range_identifier_recall: float
    context_compression_ratio: float
    semantic_routing_accuracy: float
    semantic_call_rate: float
    max_temporal_frame_budget: int
    passed: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _message(index: int, role: str, content: str) -> dict[str, Any]:
    return {
        "id": f"bench-{index}",
        "role": role,
        "content": content,
        "payload": {},
    }


def _asset(asset_id: str, kind: str, **context_values: Any) -> dict[str, Any]:
    mime = {
        "video": "video/mp4",
        "image": "image/png",
        "audio": "audio/wav",
        "text": "text/plain",
    }.get(kind, "application/octet-stream")
    meta: dict[str, Any] = {
        "kind": kind,
        "_context": {
            "kind": kind,
            "selected": True,
            "reasons": [],
            "full_content_hits": 0,
            "time_hints": [],
        },
    }
    if kind == "video":
        meta["has_audio"] = True
    meta["_context"].update(context_values)
    return {
        "id": asset_id,
        "name": f"{asset_id}.{kind}",
        "mime": mime,
        "path": "",
        "meta": meta,
    }


def run_context_benchmark() -> ContextBenchmarkResult:
    """Dependency-free mechanism benchmark for long-horizon context control.

    This benchmark does not claim end-task SOTA. It protects the mechanisms that should not
    regress while model/GPU retrieval evolves: old constraints, distant identifiers, bounded
    context, specialist routing, and hard media budgets.
    """
    history = [
        _message(
            0,
            "user",
            "目标是定位 release 偶发卡顿。必须保持 tickrate=30，不要修改 damage_coefficient。",
        )
    ]
    for index in range(1, 100):
        history.append(
            _message(
                index,
                "assistant" if index % 2 else "user",
                f"普通研发进度 {index} " + ("无关上下文" * 60),
            )
        )
    history.insert(
        7,
        _message(
            1000,
            "assistant",
            "build 1.4.7 的 shield_race 只在 release 分支复现，debug 分支没有出现。",
        ),
    )

    compiler = ContextCompiler(
        recent_messages=4,
        retrieved_messages=3,
        message_char_budget=9000,
        per_message_chars=2400,
        state_items_per_kind=8,
    )
    packet = compiler.compile(
        "继续检查 build 1.4.7 shield_race，保持之前的约束",
        history,
    )
    rendered = packet.render_task_state()
    selected_text = "\n".join(
        str(message.get("content", "")) for message in packet.messages
    )
    raw_chars = sum(len(str(message.get("content", ""))) for message in history)
    selected_chars = len(selected_text)
    compression = 1.0 - min(1.0, selected_chars / max(1, raw_chars))

    controller = EvidenceController()
    routing_cases = [
        (
            "37 秒附近护盾图标是什么状态？",
            [_asset("run", "video", time_hints=[37.0])],
            False,
        ),
        (
            "为什么 release 偶发双盾？对比录像和日志找根因",
            [_asset("run", "video"), _asset("log", "text")],
            True,
        ),
        (
            "日志里有没有资源释放顺序异常？",
            [_asset("log", "text")],
            True,
        ),
        (
            "检查 build 1.4.7 shield_race",
            [
                _asset(
                    "log",
                    "text",
                    reasons=["identifier-match", "full-content-match"],
                    full_content_hits=2,
                )
            ],
            False,
        ),
        (
            "比较两张截图中的技能图标差异",
            [_asset("a", "image"), _asset("b", "image")],
            True,
        ),
        (
            "这张截图里的 Boss 是什么状态？",
            [_asset("a", "image")],
            False,
        ),
    ]

    correct = 0
    semantic_calls = 0
    frame_budgets: list[int] = []
    for query, assets, expected_semantic in routing_cases:
        plan = controller.plan(query, assets, retriever_enabled=True)
        correct += int(plan.semantic_retrieval is expected_semantic)
        semantic_calls += int(plan.semantic_retrieval)
        frame_budgets.append(plan.temporal_frame_budget)

    constraint_recall = float(
        "tickrate=30" in rendered and "damage_coefficient" in rendered
    )
    identifier_recall = float("shield_race" in selected_text)
    routing_accuracy = correct / len(routing_cases)
    call_rate = semantic_calls / len(routing_cases)
    max_frame_budget = max(frame_budgets or [0])

    passed = bool(
        constraint_recall == 1.0
        and identifier_recall == 1.0
        and compression >= 0.80
        and routing_accuracy >= 0.95
        and max_frame_budget <= 6
    )
    return ContextBenchmarkResult(
        constraint_recall=constraint_recall,
        long_range_identifier_recall=identifier_recall,
        context_compression_ratio=round(compression, 6),
        semantic_routing_accuracy=round(routing_accuracy, 6),
        semantic_call_rate=round(call_rate, 6),
        max_temporal_frame_budget=max_frame_budget,
        passed=passed,
    )
