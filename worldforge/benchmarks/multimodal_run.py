from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
from typing import Any, Awaitable, Callable

from worldforge.benchmarks.multimodal_quality_eval import evaluate_dataset, percentile


Ranker = Callable[[str, list[dict[str, Any]], int], Awaitable[dict[str, Any]]]

_QUALITY_METRICS = (
    "recall_at_k",
    "mrr",
    "temporal_iou",
    "temporal_hit_rate",
)
_SAMPLE_METRICS = (
    *_QUALITY_METRICS,
    "build_contamination_rate",
    "forbidden_hits",
    "retrieved_media_bytes_at_k",
    "latency_p50_ms",
    "latency_p95_ms",
    "worker_lane_seconds_proxy",
)


def validate_repeat_protocol(measured_repeats: int, warmup_repeats: int) -> None:
    if measured_repeats < 1:
        raise ValueError("measured repeats must be >= 1")
    if warmup_repeats < 0:
        raise ValueError("warmup repeats must be >= 0")


def measured_run_protocol_eligible(
    *, measured_repeats: int, warmup_repeats: int
) -> bool:
    """Minimum repeat protocol required before a live run may carry a measured label."""
    return measured_repeats >= 3 and warmup_repeats >= 1


def benchmark_source_revision(root: Path) -> tuple[str, str]:
    """Return the exact benchmark-code tree identity used by this process.

    `GITHUB_SHA` is intentionally preferred in Actions because pull-request workflows execute
    the synthetic merge commit. That SHA identifies the exact tree that was tested. A live
    deployment still needs its own explicit deployment id; this value does not pretend to be a
    model/container revision.
    """
    override = os.getenv("LINGJING_BENCH_SOURCE_REVISION", "").strip()
    if override:
        return override, "LINGJING_BENCH_SOURCE_REVISION"
    github_sha = os.getenv("GITHUB_SHA", "").strip()
    if github_sha:
        return github_sha, "GITHUB_SHA"
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown", "unavailable"
    revision = completed.stdout.strip() if completed.returncode == 0 else ""
    return (revision or "unknown"), ("git-rev-parse" if revision else "unavailable")


def _mean_present(rows: list[dict[str, Any]], key: str) -> float | None:
    values = []
    for row in rows:
        value = row.get(key)
        if value is None:
            continue
        try:
            values.append(float(value))
        except (TypeError, ValueError):
            continue
    return statistics.fmean(values) if values else None


def _mean_count(rows: list[dict[str, Any]], key: str) -> int | float:
    value = _mean_present(rows, key)
    if value is None:
        return 0
    rounded = round(value)
    return int(rounded) if abs(value - rounded) < 1e-9 else value


def aggregate_evaluations(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate repeated full-corpus measurements without selecting a best run.

    Quality metrics are means across complete measured repeats. Latency percentiles are pooled
    across every case request from every measured repeat. Cost/bytes counters remain normalized
    per corpus pass, while forbidden hits are accumulated across every measured exposure.
    """
    if not rows:
        raise ValueError("at least one evaluation is required")

    result = copy.deepcopy(rows[0])
    for key in _QUALITY_METRICS:
        result[key] = _mean_present(rows, key)

    case_rows = [
        dict(case)
        for row in rows
        for case in list(row.get("case_results") or [])
    ]
    latencies = []
    total_returned_hits = 0
    total_forbidden_hits = 0
    for case in case_rows:
        try:
            latency = float(case.get("latency_ms") or 0.0)
        except (TypeError, ValueError):
            latency = 0.0
        latencies.append(max(0.0, latency))
        total_returned_hits += int(case.get("returned_hits") or 0)
        total_forbidden_hits += int(case.get("forbidden_hits") or 0)

    result["latency_p50_ms"] = percentile(latencies, 0.50)
    result["latency_p95_ms"] = percentile(latencies, 0.95)
    result["forbidden_hits"] = total_forbidden_hits
    result["build_contamination_rate"] = (
        total_forbidden_hits / total_returned_hits if total_returned_hits else 0.0
    )
    result["scope_filtered_assets"] = _mean_count(rows, "scope_filtered_assets")
    result["retrieved_media_bytes_at_k"] = _mean_count(
        rows, "retrieved_media_bytes_at_k"
    )
    result["worker_lane_seconds_proxy"] = float(
        _mean_present(rows, "worker_lane_seconds_proxy") or 0.0
    )

    backends: dict[str, int] = {}
    for row in rows:
        for backend, count in dict(row.get("backends") or {}).items():
            backends[str(backend)] = backends.get(str(backend), 0) + int(count or 0)
    result["backends"] = backends
    result["live_semantic_backend_seen"] = any(
        bool(row.get("live_semantic_backend_seen")) for row in rows
    )
    result["live_semantic_backend_seen_all_repeats"] = all(
        bool(row.get("live_semantic_backend_seen")) for row in rows
    )
    result["measured_repeats"] = len(rows)
    result["case_results_semantics"] = (
        "first-measured-repeat-only; headline metrics aggregate all measured repeats"
    )
    result["repeat_metric_samples"] = [
        {key: row.get(key) for key in _SAMPLE_METRICS}
        for row in rows
    ]
    return result


async def evaluate_repeated(
    dataset: dict[str, Any],
    ranker: Ranker,
    *,
    default_top_k: int,
    temporal_iou_threshold: float,
    measured_repeats: int,
    warmup_repeats: int,
) -> dict[str, Any]:
    validate_repeat_protocol(measured_repeats, warmup_repeats)
    for _ in range(warmup_repeats):
        await evaluate_dataset(
            dataset,
            ranker,
            default_top_k=default_top_k,
            temporal_iou_threshold=temporal_iou_threshold,
        )
    rows = []
    for _ in range(measured_repeats):
        rows.append(
            await evaluate_dataset(
                dataset,
                ranker,
                default_top_k=default_top_k,
                temporal_iou_threshold=temporal_iou_threshold,
            )
        )
    return aggregate_evaluations(rows)


def _canonical_result_digest(payload: dict[str, Any]) -> str:
    copy_payload = copy.deepcopy(payload)
    manifest = dict(copy_payload.get("run_manifest") or {})
    manifest.pop("result_digest", None)
    copy_payload["run_manifest"] = manifest
    encoded = json.dumps(
        copy_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def attach_run_manifest(
    result: dict[str, Any],
    *,
    root: Path,
    dataset_digest: str,
    measured_repeats: int,
    warmup_repeats: int,
    schedule: str,
    deployment_ids: dict[str, str],
) -> dict[str, Any]:
    revision, revision_source = benchmark_source_revision(root)
    result["run_manifest"] = {
        "schema": "lingjing-multimodal-run-manifest-v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "benchmark_code_revision": revision,
        "benchmark_code_revision_source": revision_source,
        "dataset_digest": str(dataset_digest or ""),
        "measured_repeats": int(measured_repeats),
        "warmup_repeats": int(warmup_repeats),
        "cache_state": (
            "warm-after-full-corpus-warmup"
            if warmup_repeats > 0
            else "cold-or-unspecified-no-warmup"
        ),
        "schedule": schedule,
        "deployment_ids": dict(deployment_ids),
        "latency_semantics": (
            "pooled coordinator request latency across all measured case requests"
        ),
        "worker_lane_seconds_semantics": (
            "mean per full-corpus measured pass; proxy, not exact GPU kernel time"
        ),
    }
    result["run_manifest"]["result_digest"] = _canonical_result_digest(result)
    return result


def write_result_json(payload: dict[str, Any], path: str | None) -> None:
    if not path:
        return
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(serialized, encoding="utf-8")
    temporary.replace(destination)
