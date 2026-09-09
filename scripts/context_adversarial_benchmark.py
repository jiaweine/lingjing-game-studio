from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from typing import Any

from worldforge.context import ContextCompiler


@dataclass(frozen=True)
class ScaleResult:
    messages: int
    cold_ms: float
    hot_ms: float
    selected_messages: int
    cold_candidate_messages: int
    hot_candidate_messages: int
    natural_language_candidate_messages: int
    compression_ratio: float
    constraint_recall: bool
    far_identifier_recall: bool
    natural_language_recall: bool
    current_state_correct: bool
    version_isolation: bool
    premise_awareness: bool
    state_cache_hit: bool
    retrieval_cache_hit: bool


def _message(index: int, role: str, content: str) -> dict[str, Any]:
    return {
        "id": f"adv-{index}",
        "role": role,
        "content": content,
        "payload": {},
    }


def _dense_history(count: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = [
        _message(
            0,
            "user",
            "目标是定位 release 护盾竞态。必须保持 tickrate=30，禁止修改 damage_coefficient。",
        ),
        _message(1, "user", "已确认 build 1.4.7 shield cooldown 是 9 秒。"),
        _message(2, "user", "已确认 build 2.0.0 shield cooldown 是 12 秒。"),
        _message(3, "user", "待确认 build 1.4.7 shield_race 是否只在 release 分支出现？"),
    ]

    far_index = max(50, count // 7)
    natural_index = max(70, count // 11)
    update_one = max(100, count // 3)
    update_two = max(200, (count * 4) // 5)

    templates = (
        "[render] frame={i} pipeline=deferred branch=dev hash={h:08x} gpu_wait={w}ms",
        "日志 {i}: build 1.4.{v} shield_race_candidate_{c} 未形成稳定复现，继续观察。",
        "Trace {i}: TickScheduler::Step -> ShieldSystem::Update -> frame_{f} code=E{e}",
        "研发记录 {i}：检查动画、网络同步、资源加载和 frame pacing，没有新的长期结论。",
        "config[{i}] = {{'tickrate': 30, 'worker': {w}, 'mode': 'probe-{c}'}}",
        "telemetry {i}: p95={p}ms p99={q}ms branch=feature/{c} build=1.4.{v}",
    )

    for index in range(4, count):
        role = "user" if index % 6 == 0 else "assistant"
        if index == far_index:
            content = (
                "已确认证据标识 XR-914-ZETA 对应 build 1.4.7 的 render_deadlock，"
                "只在 release/shield-race 分支采样到。"
            )
            role = "user"
        elif index == natural_index:
            content = "已确认网络抖动之后，护盾回收阶段会重复注册回调，导致结算阶段出现双重触发。"
            role = "user"
        elif index == update_one:
            content = "已确认 build 1.4.7 shield cooldown 是 7 秒。"
            role = "user"
        elif index == update_two:
            content = "已确认 build 1.4.7 shield cooldown 是 5 秒。"
            role = "user"
        elif index == update_two + 1 and index < count:
            content = "已确认 build 1.4.7 shield_race 只在 release 分支出现。"
            role = "user"
        elif index == update_two + 2 and index < count:
            content = "如果已确认 build 2.1.0 shield cooldown 是 3 秒，再执行新公式回归。"
            role = "user"
        else:
            template = templates[index % len(templates)]
            content = template.format(
                i=index,
                h=(index * 2654435761) & 0xFFFFFFFF,
                w=index % 23,
                v=index % 19,
                c=index % 31,
                f=index % 997,
                e=index % 91,
                p=8 + index % 37,
                q=20 + index % 71,
            )
        rows.append(_message(index, role, content))
    return rows


def _contains(rows: list[dict[str, Any]], needle: str) -> bool:
    return any(needle in str(row.get("content", "")) for row in rows)


def _verified_text(packet) -> str:
    return "\n".join(
        str(row.get("text", ""))
        for row in packet.task_state.get("verified_facts", [])
    )


def _run_scale(count: int) -> ScaleResult:
    compiler = ContextCompiler(
        recent_messages=4,
        retrieved_messages=3,
        message_char_budget=9000,
        per_message_chars=2400,
        state_items_per_kind=12,
    )
    history = _dense_history(count)
    query = (
        "继续排查 build 1.4.7 的 XR-914-ZETA render_deadlock；"
        "保持既有约束，并只使用当前有效 shield cooldown。"
    )

    started = time.perf_counter()
    cold = compiler.compile(query, history)
    cold_ms = (time.perf_counter() - started) * 1000.0

    hot_history = [
        *history,
        _message(count, "user", "继续当前排查，不改变已确认的 build 1.4.7 状态。"),
    ]
    started = time.perf_counter()
    hot = compiler.compile(query, hot_history)
    hot_ms = (time.perf_counter() - started) * 1000.0

    natural = compiler.compile(
        "网络抖动之后，护盾回收为什么会重复注册回调并造成双重触发？",
        hot_history,
    )

    state_text = cold.render_task_state()
    verified = _verified_text(cold)
    current_state = "build 1.4.7 shield cooldown 是 5 秒" in verified
    stale_state_absent = all(
        value not in verified
        for value in (
            "build 1.4.7 shield cooldown 是 9 秒",
            "build 1.4.7 shield cooldown 是 7 秒",
        )
    )
    version_isolation = "build 2.0.0 shield cooldown 是 12 秒" in verified
    premise_awareness = "build 2.1.0 shield cooldown 是 3 秒" not in verified

    return ScaleResult(
        messages=count,
        cold_ms=round(cold_ms, 3),
        hot_ms=round(hot_ms, 3),
        selected_messages=len(cold.messages),
        cold_candidate_messages=cold.candidate_history_messages,
        hot_candidate_messages=hot.candidate_history_messages,
        natural_language_candidate_messages=natural.candidate_history_messages,
        compression_ratio=round(1.0 - (len(cold.messages) / max(1, count)), 6),
        constraint_recall="tickrate=30" in state_text,
        far_identifier_recall=_contains(cold.messages, "XR-914-ZETA"),
        natural_language_recall=_contains(natural.messages, "重复注册回调"),
        current_state_correct=current_state and stale_state_absent,
        version_isolation=version_isolation,
        premise_awareness=premise_awareness,
        state_cache_hit=bool(hot.state_cache_hit),
        retrieval_cache_hit=bool(hot.retrieval_cache_hit),
    )


def main() -> None:
    results = [_run_scale(10_000), _run_scale(50_000)]
    output = {
        "benchmark": "context-adversarial-scale-v2",
        "results": [asdict(row) for row in results],
        "cold_growth_ratio_50k_over_10k": round(
            results[1].cold_ms / max(results[0].cold_ms, 0.001), 3
        ),
        "hot_growth_ratio_50k_over_10k": round(
            results[1].hot_ms / max(results[0].hot_ms, 0.001), 3
        ),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))

    for row in results:
        assert row.constraint_recall
        assert row.far_identifier_recall
        assert row.natural_language_recall
        assert row.current_state_correct
        assert row.version_isolation
        assert row.premise_awareness
        assert row.state_cache_hit
        assert row.retrieval_cache_hit
        assert row.selected_messages <= 8
        assert row.compression_ratio >= 0.999
        assert row.hot_ms < 100.0, row

    assert output["cold_growth_ratio_50k_over_10k"] < 8.0, output
    assert results[0].cold_ms < 2_000.0, results[0]
    assert results[1].cold_ms < 8_000.0, results[1]


if __name__ == "__main__":
    main()
