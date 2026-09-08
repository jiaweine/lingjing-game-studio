from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worldforge.benchmarks.long_horizon_model_eval import (
    MODEL_PROTOCOL,
    aggregate_model_scores,
    score_model_response,
    validate_model_dataset,
)
from worldforge.context.compiler import ContextCompiler
from worldforge.providers import ProviderRegistry


_SYSTEM = (
    "你正在接受长程项目连续性评测。只根据提供的历史和当前问题回答；"
    "区分已确认事实、过期值、未支持信息和流程约束。没有证据时明确表示无法确认，"
    "不要补造事实。"
)


def protocol_smoke_dataset() -> dict:
    filler = [
        {"id": f"filler-{index}", "role": "assistant" if index % 2 else "user", "content": f"普通讨论 {index}"}
        for index in range(12)
    ]
    return {
        "name": MODEL_PROTOCOL["name"],
        "protocol_version": MODEL_PROTOCOL["protocol_version"],
        "evidence_class": "synthetic-protocol-smoke",
        "frozen": False,
        "heldout_policy": {"development_excluded": False},
        "cases": [
            {
                "id": "qa-old-fact",
                "category": "qa",
                "history": [
                    {"id": "old", "role": "user", "content": "已确认护盾冷却是 5 秒。"},
                    *filler,
                ],
                "long_range_anchor_index": 0,
                "query": "护盾冷却是多少？",
                "rubric": {"required_any": [["5 秒", "5秒"]], "forbidden_any": ["6 秒", "6秒"]},
                "fixture_response": "根据已确认历史，护盾冷却是 5 秒。",
            },
            {
                "id": "update-latest",
                "category": "update",
                "history": [
                    {"id": "old", "role": "user", "content": "已确认 tickrate=30。"},
                    *filler,
                    {"id": "new", "role": "user", "content": "更新：已确认 tickrate=60。"},
                ],
                "long_range_anchor_index": 0,
                "query": "当前 tickrate 是多少？",
                "rubric": {"required_any": [["tickrate=60", "tickrate = 60"]], "forbidden_any": ["tickrate=30"]},
                "fixture_response": "当前已确认值是 tickrate=60。",
            },
            {
                "id": "abstain-unsupported",
                "category": "abstention",
                "history": [
                    {"id": "old", "role": "user", "content": "已确认 boss stagger threshold=20。"},
                    *filler,
                ],
                "long_range_anchor_index": 0,
                "query": "最终 release 的 GPU crash 根因是什么？",
                "rubric": {
                    "abstention_required": True,
                    "abstention_markers": ["无法确认", "没有证据", "不足以确认"],
                    "forbidden_any": ["根因是驱动", "根因是显存"],
                },
                "fixture_response": "现有历史没有证据，无法确认 GPU crash 根因。",
            },
            {
                "id": "workflow-order",
                "category": "workflow",
                "history": [
                    {"id": "old", "role": "user", "content": "发布流程必须先跑 smoke_suite，再跑 regression_suite，最后人工签字。"},
                    *filler,
                ],
                "long_range_anchor_index": 0,
                "query": "发布前流程是什么？",
                "rubric": {
                    "workflow_order": [
                        ["smoke_suite"],
                        ["regression_suite"],
                        ["人工签字"],
                    ]
                },
                "fixture_response": "先运行 smoke_suite，然后 regression_suite，最后人工签字。",
            },
        ],
    }


def _baseline_messages(case: dict) -> list[dict]:
    history = [
        {"role": row.get("role"), "content": str(row.get("content") or "")}
        for row in list(case.get("history") or [])[-8:]
        if row.get("role") in {"user", "assistant"}
    ]
    return [{"role": "system", "content": _SYSTEM}, *history, {"role": "user", "content": str(case["query"])}]


def _contextos_messages(case: dict, compiler: ContextCompiler) -> tuple[list[dict], dict]:
    history = [dict(row or {}) for row in list(case.get("history") or [])]
    packet = compiler.compile(str(case["query"]), history)
    system = _SYSTEM + "\n\n【ContextOS 结构化任务状态】\n" + packet.render_task_state()
    selected = [
        {"role": row.get("role"), "content": str(row.get("content") or "")}
        for row in packet.messages
        if row.get("role") in {"user", "assistant"}
    ]
    return [
        {"role": "system", "content": system},
        *selected,
        {"role": "user", "content": str(case["query"])},
    ], packet.stats()


async def _run_live(dataset: dict, provider_key: str) -> dict:
    registry = ProviderRegistry()
    provider = registry.choose(provider_key, [])
    if provider is None or not provider.info.configured:
        raise SystemExit(f"configured provider unavailable: {provider_key}")
    compiler = ContextCompiler()
    rows: dict[str, list] = {"baseline_last8": [], "contextos": []}
    latencies: dict[str, list[float]] = {"baseline_last8": [], "contextos": []}
    telemetry: dict[str, list[dict]] = {"baseline_last8": [], "contextos": []}
    per_case: list[dict] = []

    for case in list(dataset.get("cases") or []):
        base_messages = _baseline_messages(case)
        context_messages, context_stats = _contextos_messages(case, compiler)
        case_result = {"id": case["id"], "category": case["category"], "context_stats": context_stats}
        for label, messages in (("baseline_last8", base_messages), ("contextos", context_messages)):
            started = time.perf_counter()
            response = await provider.chat(messages=messages, assets=[])
            elapsed = (time.perf_counter() - started) * 1000.0
            score = score_model_response(case, response)
            rows[label].append(score)
            latencies[label].append(elapsed)
            telemetry[label].append(dict(provider.request_telemetry() or {}))
            case_result[label] = {
                "response": response,
                "score": score.to_dict(),
                "latency_ms": round(elapsed, 3),
            }
        per_case.append(case_result)

    systems: dict[str, dict] = {}
    for label in ("baseline_last8", "contextos"):
        metrics = aggregate_model_scores(rows[label])
        values = sorted(latencies[label])
        metrics.update(
            {
                "latency_mean_ms": round(statistics.mean(values), 3) if values else None,
                "latency_p95_ms": round(values[max(0, int(len(values) * 0.95) - 1)], 3) if values else None,
                "provider_telemetry": telemetry[label],
            }
        )
        systems[label] = metrics
    return {
        "provider": {
            "key": provider.info.key,
            "name": provider.info.name,
            "vendor": provider.info.vendor,
            "model": provider.info.model,
        },
        "systems": systems,
        "delta_contextos_minus_baseline": {
            key: round(float(systems["contextos"][key]) - float(systems["baseline_last8"][key]), 6)
            for key in (
                "case_pass_rate",
                "required_anchor_recall",
                "abstention_compliance",
                "workflow_order_compliance",
            )
        },
        "per_case": per_case,
    }


def _smoke(dataset: dict) -> dict:
    scores = [score_model_response(case, str(case.get("fixture_response") or "")) for case in dataset["cases"]]
    return {
        "fixture_scorer": aggregate_model_scores(scores),
        "pack_smoke": [
            {
                "id": case["id"],
                "baseline_messages": len(_baseline_messages(case)),
                "contextos": _contextos_messages(case, ContextCompiler())[1],
            }
            for case in dataset["cases"]
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset")
    parser.add_argument("--provider")
    parser.add_argument("--output")
    parser.add_argument("--require-quality-eligible-dataset", action="store_true")
    parser.add_argument("--require-live-provider", action="store_true")
    args = parser.parse_args()

    if args.dataset:
        path = Path(args.dataset).resolve()
        dataset = json.loads(path.read_text(encoding="utf-8"))
        validation = validate_model_dataset(dataset)
        if not validation["structurally_valid"]:
            raise SystemExit("invalid model benchmark dataset: " + "; ".join(validation["errors"][:5]))
        if args.require_quality_eligible_dataset and not validation["strict_quality_eligible"]:
            raise SystemExit("dataset is not strict quality eligible: " + "; ".join(validation["quality_blockers"][:5]))
        if not args.provider:
            raise SystemExit("--provider is required with an external dataset")
        live = asyncio.run(_run_live(dataset, args.provider))
        result = {
            "protocol": MODEL_PROTOCOL,
            "dataset_validation": validation,
            **live,
            "evidence_class": (
                "measured-heldout-anchor-rubric-only"
                if validation["strict_quality_eligible"]
                else "development-live-anchor-rubric-only"
            ),
            "quality_claim": "anchor-rubric-only-not-general-semantic-quality",
        }
    else:
        if args.require_live_provider:
            raise SystemExit("--require-live-provider needs --dataset and --provider")
        dataset = protocol_smoke_dataset()
        result = {
            "protocol": MODEL_PROTOCOL,
            "dataset_validation": validate_model_dataset(dataset),
            **_smoke(dataset),
            "evidence_class": "synthetic-protocol-smoke-not-model-quality-evidence",
            "quality_claim": "none-protocol-smoke",
        }

    if args.output:
        output = Path(args.output).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
