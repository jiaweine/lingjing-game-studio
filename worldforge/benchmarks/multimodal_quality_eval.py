from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import math
import statistics
from typing import Any, Awaitable, Callable

from worldforge.context.scoped_retrieval import scope_eligible_assets


Ranker = Callable[[str, list[dict[str, Any]], int], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class QualityCaseResult:
    case_id: str
    recall_at_k: float
    reciprocal_rank: float
    temporal_iou: float | None
    temporal_hit: float | None
    forbidden_hits: int
    returned_hits: int
    scope_filtered_assets: int
    retrieved_media_bytes: int
    latency_ms: float
    worker_lane_seconds_proxy: float
    backend: str


def interval_iou(
    predicted_start: float | None,
    predicted_end: float | None,
    truth_start: float | None,
    truth_end: float | None,
) -> float:
    values = (predicted_start, predicted_end, truth_start, truth_end)
    if any(value is None for value in values):
        return 0.0
    ps, pe, ts, te = (float(value) for value in values)
    if pe <= ps or te <= ts:
        return 0.0
    intersection = max(0.0, min(pe, te) - max(ps, ts))
    union = max(pe, te) - min(ps, ts)
    return intersection / union if union > 0 else 0.0


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    rows = sorted(float(value) for value in values)
    if len(rows) == 1:
        return rows[0]
    position = max(0.0, min(1.0, q)) * (len(rows) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return rows[low]
    fraction = position - low
    return rows[low] * (1.0 - fraction) + rows[high] * fraction


def _asset_size(asset: dict[str, Any]) -> int:
    raw = asset.get("size")
    if raw is not None:
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            pass
    path = str(asset.get("path") or "")
    if path:
        try:
            return max(0, Path(path).stat().st_size)
        except OSError:
            pass
    return 0


def _kind(asset: dict[str, Any]) -> str:
    meta = asset.get("meta", {}) or {}
    value = str(meta.get("kind") or "")
    if value and value != "file":
        return value
    mime = str(asset.get("mime") or "")
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("audio/"):
        return "audio"
    if mime.startswith("text/"):
        return "text"
    return value or "file"


def _worker_lane_seconds(debug: dict[str, Any]) -> float:
    total_ms = 0.0
    for key in ("visual_latency_ms", "audio_latency_ms"):
        value = debug.get(key)
        if value is None:
            continue
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(parsed) and parsed > 0:
            total_ms += parsed
    return total_ms / 1000.0


async def evaluate_case(
    case: dict[str, Any],
    ranker: Ranker,
    *,
    default_top_k: int = 5,
    temporal_iou_threshold: float = 0.3,
) -> QualityCaseResult:
    all_assets = [dict(row) for row in list(case.get("assets") or [])]
    eligible_assets = scope_eligible_assets(all_assets)
    top_k = max(1, int(case.get("top_k") or default_top_k))
    response = await ranker(str(case.get("query") or ""), eligible_assets, top_k)
    hits = list(response.get("hits") or [])[:top_k]

    relevant = list(case.get("relevant") or [])
    relevant_ids = {str(row.get("asset_id") or "") for row in relevant}
    relevant_ids.discard("")
    ranked_ids = [str(hit.get("asset_id") or "") for hit in hits]
    recalled = len(relevant_ids.intersection(ranked_ids))
    recall = recalled / len(relevant_ids) if relevant_ids else 1.0

    reciprocal_rank = 0.0
    for index, asset_id in enumerate(ranked_ids, start=1):
        if asset_id in relevant_ids:
            reciprocal_rank = 1.0 / index
            break

    temporal_truth = [
        row
        for row in relevant
        if row.get("start") is not None and row.get("end") is not None
    ]
    temporal_iou_value: float | None = None
    temporal_hit: float | None = None
    if temporal_truth:
        best = 0.0
        for truth in temporal_truth:
            asset_id = str(truth.get("asset_id") or "")
            for hit in hits:
                if str(hit.get("asset_id") or "") != asset_id:
                    continue
                best = max(
                    best,
                    interval_iou(
                        hit.get("start"),
                        hit.get("end"),
                        truth.get("start"),
                        truth.get("end"),
                    ),
                )
        temporal_iou_value = best
        temporal_hit = float(best >= temporal_iou_threshold)

    forbidden = {str(value) for value in list(case.get("forbidden_asset_ids") or [])}
    forbidden.discard("")
    forbidden_hits = sum(1 for asset_id in ranked_ids if asset_id in forbidden)

    by_id = {str(asset.get("id") or ""): asset for asset in eligible_assets}
    media_bytes = 0
    seen_media: set[str] = set()
    for asset_id in ranked_ids:
        asset = by_id.get(asset_id)
        if asset is None or asset_id in seen_media:
            continue
        if _kind(asset) in {"image", "video", "audio"}:
            media_bytes += _asset_size(asset)
            seen_media.add(asset_id)

    latency = response.get("latency_ms")
    try:
        latency_ms = float(latency) if latency is not None else 0.0
    except (TypeError, ValueError):
        latency_ms = 0.0
    if not math.isfinite(latency_ms):
        latency_ms = 0.0

    return QualityCaseResult(
        case_id=str(case.get("id") or "unnamed"),
        recall_at_k=recall,
        reciprocal_rank=reciprocal_rank,
        temporal_iou=temporal_iou_value,
        temporal_hit=temporal_hit,
        forbidden_hits=forbidden_hits,
        returned_hits=len(hits),
        scope_filtered_assets=len(all_assets) - len(eligible_assets),
        retrieved_media_bytes=media_bytes,
        latency_ms=max(0.0, latency_ms),
        worker_lane_seconds_proxy=_worker_lane_seconds(dict(response.get("debug") or {})),
        backend=str(response.get("backend") or "unknown"),
    )


async def evaluate_dataset(
    dataset: dict[str, Any],
    ranker: Ranker,
    *,
    default_top_k: int = 5,
    temporal_iou_threshold: float = 0.3,
) -> dict[str, Any]:
    cases = list(dataset.get("cases") or [])
    results = [
        await evaluate_case(
            case,
            ranker,
            default_top_k=default_top_k,
            temporal_iou_threshold=temporal_iou_threshold,
        )
        for case in cases
    ]
    temporal = [row.temporal_iou for row in results if row.temporal_iou is not None]
    temporal_hits = [row.temporal_hit for row in results if row.temporal_hit is not None]
    backends: dict[str, int] = {}
    for row in results:
        backends[row.backend] = backends.get(row.backend, 0) + 1
    latencies = [row.latency_ms for row in results]
    total_hits = sum(row.returned_hits for row in results)
    forbidden_hits = sum(row.forbidden_hits for row in results)
    return {
        "benchmark": "lingjing-multimodal-quality-v1",
        "dataset": str(dataset.get("name") or "unnamed"),
        "evidence_class": str(dataset.get("evidence_class") or "unspecified"),
        "cases": len(results),
        "recall_at_k": statistics.fmean(row.recall_at_k for row in results) if results else 0.0,
        "mrr": statistics.fmean(row.reciprocal_rank for row in results) if results else 0.0,
        "temporal_iou": statistics.fmean(temporal) if temporal else None,
        "temporal_hit_rate": statistics.fmean(temporal_hits) if temporal_hits else None,
        "build_contamination_rate": forbidden_hits / total_hits if total_hits else 0.0,
        "forbidden_hits": forbidden_hits,
        "scope_filtered_assets": sum(row.scope_filtered_assets for row in results),
        "retrieved_media_bytes_at_k": sum(row.retrieved_media_bytes for row in results),
        "latency_p50_ms": percentile(latencies, 0.50),
        "latency_p95_ms": percentile(latencies, 0.95),
        "worker_lane_seconds_proxy": sum(row.worker_lane_seconds_proxy for row in results),
        "backends": backends,
        "live_semantic_backend_seen": any(
            backend not in {"lexical-fallback", "unknown"} for backend in backends
        ),
        "case_results": [
            {
                "case_id": row.case_id,
                "recall_at_k": row.recall_at_k,
                "reciprocal_rank": row.reciprocal_rank,
                "temporal_iou": row.temporal_iou,
                "temporal_hit": row.temporal_hit,
                "forbidden_hits": row.forbidden_hits,
                "returned_hits": row.returned_hits,
                "scope_filtered_assets": row.scope_filtered_assets,
                "retrieved_media_bytes": row.retrieved_media_bytes,
                "latency_ms": row.latency_ms,
                "worker_lane_seconds_proxy": row.worker_lane_seconds_proxy,
                "backend": row.backend,
            }
            for row in results
        ],
    }
