from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.multimodal_quality_benchmark import (
    _load_dataset,
    live_ranker,
    prepare_external_dataset,
    product_lexical_rank,
)
from worldforge.benchmarks.multimodal_quality_eval import evaluate_dataset


_DELTA_METRICS = (
    "recall_at_k",
    "mrr",
    "temporal_iou",
    "temporal_hit_rate",
    "build_contamination_rate",
    "retrieved_media_bytes_at_k",
    "latency_p50_ms",
    "latency_p95_ms",
    "worker_lane_seconds_proxy",
)


def parse_backend_spec(value: str) -> tuple[str, str]:
    name, separator, endpoint = str(value or "").partition("=")
    name = name.strip()
    endpoint = endpoint.strip().rstrip("/")
    if not separator or not name or not endpoint:
        raise argparse.ArgumentTypeError("backend must be NAME=URL")
    if name == "baseline":
        raise argparse.ArgumentTypeError("backend name 'baseline' is reserved")
    return name, endpoint


def metric_deltas(
    current: dict[str, Any], baseline: dict[str, Any]
) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for key in _DELTA_METRICS:
        left = current.get(key)
        right = baseline.get(key)
        if left is None or right is None:
            out[key] = None
            continue
        try:
            out[key] = float(left) - float(right)
        except (TypeError, ValueError):
            out[key] = None
    return out


async def evaluate_backend(
    *,
    label: str,
    dataset: dict[str, Any],
    ranker,
    top_k: int,
    temporal_iou_threshold: float,
    mode: str,
) -> dict[str, Any]:
    result = await evaluate_dataset(
        dataset,
        ranker,
        default_top_k=top_k,
        temporal_iou_threshold=temporal_iou_threshold,
    )
    result["label"] = label
    result["mode"] = mode
    return result


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    corpus_validation = None
    if args.dataset:
        dataset, corpus_validation = prepare_external_dataset(
            args.dataset,
            verify_files=not args.skip_file_hash_verification,
        )
    else:
        dataset = _load_dataset(None)

    backend_specs = list(args.backend or [])
    seen_names: set[str] = set()
    for name, _endpoint in backend_specs:
        if name in seen_names:
            raise SystemExit(f"duplicate backend label: {name}")
        seen_names.add(name)

    baseline = await evaluate_backend(
        label="baseline",
        dataset=dataset,
        ranker=product_lexical_rank,
        top_k=args.top_k,
        temporal_iou_threshold=args.temporal_iou_threshold,
        mode="product-lexical-fallback",
    )
    runs = [baseline]
    comparisons = []
    for name, endpoint in backend_specs:
        current = await evaluate_backend(
            label=name,
            dataset=dataset,
            ranker=live_ranker(endpoint, args.timeout),
            top_k=args.top_k,
            temporal_iou_threshold=args.temporal_iou_threshold,
            mode="live-sidecar",
        )
        runs.append(current)
        comparisons.append(
            {
                "label": name,
                "vs": "baseline",
                "deltas": metric_deltas(current, baseline),
            }
        )

    quality_eligible = bool(
        corpus_validation and corpus_validation.get("strict_quality_eligible")
    )
    result = {
        "benchmark": "lingjing-multimodal-quality-matrix-v1",
        "dataset": str(dataset.get("name") or "unnamed"),
        "evidence_class": str(dataset.get("evidence_class") or "unspecified"),
        "external_dataset": bool(args.dataset),
        "backend_order": [row["label"] for row in runs],
        "runs": runs,
        "comparisons": comparisons,
        "quality_claim": (
            "controlled-live-comparison-on-frozen-heldout-corpus"
            if quality_eligible and bool(backend_specs)
            else (
                "none-unvalidated-external-dataset"
                if args.dataset
                else "none-protocol-smoke"
            )
        ),
    }
    if corpus_validation is not None:
        result["corpus_validation"] = corpus_validation
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset")
    parser.add_argument(
        "--backend",
        action="append",
        default=[],
        type=parse_backend_spec,
        help="repeatable NAME=URL coordinator endpoint",
    )
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--temporal-iou-threshold", type=float, default=0.3)
    parser.add_argument("--require-zero-contamination", action="store_true")
    parser.add_argument("--require-semantic-backend", action="store_true")
    parser.add_argument("--require-quality-eligible-corpus", action="store_true")
    parser.add_argument("--skip-file-hash-verification", action="store_true")
    args = parser.parse_args()

    result = asyncio.run(_run(args))
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if args.require_zero_contamination:
        contaminated = [
            row["label"]
            for row in result["runs"]
            if int(row.get("forbidden_hits") or 0) != 0
        ]
        if contaminated:
            raise SystemExit(
                "build contamination detected: " + ", ".join(contaminated)
            )
    if args.require_semantic_backend:
        missing = [
            row["label"]
            for row in result["runs"]
            if row["label"] != "baseline"
            and not row.get("live_semantic_backend_seen")
        ]
        if missing:
            raise SystemExit(
                "semantic backend was not observed: " + ", ".join(missing)
            )
    if args.require_quality_eligible_corpus:
        report = dict(result.get("corpus_validation") or {})
        if not report.get("strict_quality_eligible"):
            raise SystemExit("corpus is not eligible for measured quality claims")


if __name__ == "__main__":
    main()
