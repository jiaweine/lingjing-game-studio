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
from worldforge.benchmarks.multimodal_corpus import canonical_corpus_digest
from worldforge.benchmarks.multimodal_quality_eval import evaluate_dataset
from worldforge.benchmarks.multimodal_run import (
    aggregate_evaluations,
    attach_run_manifest,
    measured_run_protocol_eligible,
    write_result_json,
)


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


def parse_deployment_spec(value: str) -> tuple[str, str]:
    name, separator, deployment_id = str(value or "").partition("=")
    name = name.strip()
    deployment_id = deployment_id.strip()
    if not separator or not name or not deployment_id:
        raise argparse.ArgumentTypeError("deployment must be NAME=IMMUTABLE_ID")
    if name == "baseline":
        raise argparse.ArgumentTypeError("baseline deployment id is derived from benchmark code")
    return name, deployment_id


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


def _rotated(rows: list[dict[str, Any]], cycle: int) -> list[dict[str, Any]]:
    if not rows:
        return []
    offset = cycle % len(rows)
    return rows[offset:] + rows[:offset]


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


def _matrix_run_blockers(
    *,
    runs: list[dict[str, Any]],
    live_labels: list[str],
    deployment_ids: dict[str, str],
    measured_repeats: int,
    warmup_repeats: int,
) -> list[str]:
    blockers = []
    if not live_labels:
        blockers.append("at least one live backend is required")
    missing_deployments = [label for label in live_labels if not deployment_ids.get(label)]
    if missing_deployments:
        blockers.append(
            "immutable deployment id missing for: " + ", ".join(missing_deployments)
        )
    if not measured_run_protocol_eligible(
        measured_repeats=measured_repeats,
        warmup_repeats=warmup_repeats,
    ):
        blockers.append("requires >=3 measured repeats and >=1 full-corpus warmup")
    no_semantic = [
        row["label"]
        for row in runs
        if row["label"] in live_labels
        and not row.get("live_semantic_backend_seen_all_repeats")
    ]
    if no_semantic:
        blockers.append(
            "semantic backend not observed in every repeat for: " + ", ".join(no_semantic)
        )
    contaminated = [
        row["label"] for row in runs if int(row.get("forbidden_hits") or 0) != 0
    ]
    if contaminated:
        blockers.append("scope/build contamination detected in: " + ", ".join(contaminated))
    return blockers


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

    deployment_specs = dict(args.deployment or [])
    if len(deployment_specs) != len(list(args.deployment or [])):
        raise SystemExit("duplicate deployment label")
    unknown_deployments = sorted(set(deployment_specs) - seen_names)
    if unknown_deployments:
        raise SystemExit(
            "deployment id provided for unknown backend: " + ", ".join(unknown_deployments)
        )

    definitions: list[dict[str, Any]] = [
        {
            "label": "baseline",
            "mode": "product-lexical-fallback",
            "ranker": product_lexical_rank,
            "deployment_id": "product-lexical-fallback@benchmark-code-revision",
        }
    ]
    for name, endpoint in backend_specs:
        definitions.append(
            {
                "label": name,
                "mode": "live-sidecar",
                "ranker": live_ranker(endpoint, args.timeout),
                "deployment_id": deployment_specs.get(name, ""),
            }
        )

    for cycle in range(args.warmup_repeats):
        for definition in _rotated(definitions, cycle):
            await evaluate_backend(
                label=definition["label"],
                dataset=dataset,
                ranker=definition["ranker"],
                top_k=args.top_k,
                temporal_iou_threshold=args.temporal_iou_threshold,
                mode=definition["mode"],
            )

    measurements: dict[str, list[dict[str, Any]]] = {
        definition["label"]: [] for definition in definitions
    }
    for repeat in range(args.repeats):
        cycle = args.warmup_repeats + repeat
        for definition in _rotated(definitions, cycle):
            measurements[definition["label"]].append(
                await evaluate_backend(
                    label=definition["label"],
                    dataset=dataset,
                    ranker=definition["ranker"],
                    top_k=args.top_k,
                    temporal_iou_threshold=args.temporal_iou_threshold,
                    mode=definition["mode"],
                )
            )

    runs = []
    for definition in definitions:
        row = aggregate_evaluations(measurements[definition["label"]])
        row["label"] = definition["label"]
        row["mode"] = definition["mode"]
        row["deployment_id"] = definition["deployment_id"] or "unspecified-live-deployment"
        runs.append(row)

    baseline = runs[0]
    comparisons = [
        {
            "label": row["label"],
            "vs": "baseline",
            "deltas": metric_deltas(row, baseline),
        }
        for row in runs[1:]
    ]

    corpus_quality_eligible = bool(
        corpus_validation and corpus_validation.get("strict_quality_eligible")
    )
    live_labels = [name for name, _endpoint in backend_specs]
    raw_deployment_ids = {
        definition["label"]: str(definition.get("deployment_id") or "")
        for definition in definitions
    }
    deployment_ids = {
        row["label"]: str(row.get("deployment_id") or "")
        for row in runs
    }
    run_blockers = _matrix_run_blockers(
        runs=runs,
        live_labels=live_labels,
        deployment_ids=raw_deployment_ids,
        measured_repeats=args.repeats,
        warmup_repeats=args.warmup_repeats,
    )
    measured_run_eligible = bool(corpus_quality_eligible and not run_blockers)

    if not args.dataset:
        quality_claim = "none-protocol-smoke"
    elif not corpus_quality_eligible:
        quality_claim = "none-unvalidated-external-dataset"
    elif measured_run_eligible:
        quality_claim = "controlled-live-comparison-on-frozen-heldout-corpus"
    else:
        quality_claim = "none-incomplete-live-run-provenance"

    result = {
        "benchmark": "lingjing-multimodal-quality-matrix-v1",
        "dataset": str(dataset.get("name") or "unnamed"),
        "evidence_class": str(dataset.get("evidence_class") or "unspecified"),
        "external_dataset": bool(args.dataset),
        "backend_order": [row["label"] for row in runs],
        "runs": runs,
        "comparisons": comparisons,
        "measured_run_eligible": measured_run_eligible,
        "measured_run_blockers": run_blockers if args.dataset else [],
        "quality_claim": quality_claim,
    }
    if corpus_validation is not None:
        result["corpus_validation"] = corpus_validation

    dataset_digest = (
        str(corpus_validation.get("corpus_digest") or "")
        if corpus_validation
        else canonical_corpus_digest(dataset)
    )
    attach_run_manifest(
        result,
        root=ROOT,
        dataset_digest=dataset_digest,
        measured_repeats=args.repeats,
        warmup_repeats=args.warmup_repeats,
        schedule="balanced-backend-rotation-full-corpus-v1",
        deployment_ids=deployment_ids,
    )
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
    parser.add_argument(
        "--deployment",
        action="append",
        default=[],
        type=parse_deployment_spec,
        help="repeatable NAME=IMMUTABLE_ID for each live backend",
    )
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--temporal-iou-threshold", type=float, default=0.3)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--warmup-repeats", type=int, default=0)
    parser.add_argument("--output", help="optional JSON result artifact path")
    parser.add_argument("--require-zero-contamination", action="store_true")
    parser.add_argument("--require-semantic-backend", action="store_true")
    parser.add_argument("--require-quality-eligible-corpus", action="store_true")
    parser.add_argument("--require-measured-quality-run", action="store_true")
    parser.add_argument("--skip-file-hash-verification", action="store_true")
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be >= 1")
    if args.warmup_repeats < 0:
        parser.error("--warmup-repeats must be >= 0")

    result = asyncio.run(_run(args))
    write_result_json(result, args.output)
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
        live_rows = [row for row in result["runs"] if row["label"] != "baseline"]
        if not live_rows:
            raise SystemExit("semantic backend required but no live backend configured")
        missing = [
            row["label"]
            for row in live_rows
            if not row.get("live_semantic_backend_seen_all_repeats")
        ]
        if missing:
            raise SystemExit(
                "semantic backend was not observed in every measured repeat: "
                + ", ".join(missing)
            )
    if args.require_quality_eligible_corpus:
        report = dict(result.get("corpus_validation") or {})
        if not report.get("strict_quality_eligible"):
            raise SystemExit("corpus is not eligible for measured quality claims")
    if args.require_measured_quality_run and not result.get("measured_run_eligible"):
        raise SystemExit(
            "run is not eligible for measured quality claims: "
            + "; ".join(result.get("measured_run_blockers") or [])
        )


if __name__ == "__main__":
    main()
