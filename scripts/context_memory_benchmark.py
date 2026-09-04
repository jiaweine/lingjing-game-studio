from __future__ import annotations

import argparse
import json
import time

from worldforge.context import ContextCompiler


def build_history(count: int):
    rows = [
        {
            "id": "goal",
            "role": "user",
            "content": (
                "目标是定位编辑器卡顿。必须保持 tickrate=30，"
                "不要修改 damage_coefficient。"
            ),
        }
    ]
    target_index = max(2, count // 5)
    for index in range(1, count):
        if index == target_index:
            content = (
                "日志显示 build 1.4.7 的 shield_race "
                "只在 release 分支出现。"
            )
        else:
            content = (
                f"第 {index} 轮普通研发记录，检查 frame pipeline 和阶段进度。"
            )
        rows.append(
            {
                "id": f"m-{index}",
                "role": "assistant" if index % 2 else "user",
                "content": content,
            }
        )
    return rows


def contains(rows, needle: str) -> bool:
    return any(needle in str(row.get("content", "")) for row in rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--messages", type=int, default=5000)
    args = parser.parse_args()
    count = max(30, args.messages)
    history = build_history(count)
    query = "build 1.4.7 的 shield_race 是什么结论？继续保持之前约束。"

    baseline = history[-8:]
    compiler = ContextCompiler(
        recent_messages=4,
        retrieved_messages=3,
        message_char_budget=9000,
        per_message_chars=2400,
    )

    started = time.perf_counter()
    cold = compiler.compile(query, history)
    cold_ms = (time.perf_counter() - started) * 1000

    next_history = [
        *history,
        {
            "id": f"m-{count}",
            "role": "user",
            "content": "继续按之前约束，不要改变 tickrate。",
        },
    ]
    started = time.perf_counter()
    hot = compiler.compile(query, next_history)
    hot_ms = (time.perf_counter() - started) * 1000

    state_text = cold.render_task_state()
    result = {
        "messages": count,
        "baseline_last8": {
            "constraint_recall": contains(baseline, "tickrate=30"),
            "far_fact_recall": contains(baseline, "shield_race"),
            "model_facing_messages": len(baseline),
        },
        "context_compiler": {
            "constraint_recall": "tickrate=30" in state_text,
            "far_fact_recall": contains(cold.messages, "shield_race"),
            "cold_ms": round(cold_ms, 3),
            "hot_ms": round(hot_ms, 3),
            "cold": cold.stats(),
            "hot": hot.stats(),
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))

    assert result["baseline_last8"]["constraint_recall"] is False
    assert result["baseline_last8"]["far_fact_recall"] is False
    assert result["context_compiler"]["constraint_recall"] is True
    assert result["context_compiler"]["far_fact_recall"] is True
    assert hot.state_cache_hit is True
    assert hot.retrieval_cache_hit is True


if __name__ == "__main__":
    main()
