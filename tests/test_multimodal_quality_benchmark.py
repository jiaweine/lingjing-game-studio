from __future__ import annotations

import asyncio
import json
from pathlib import Path
import subprocess
import sys

from worldforge.benchmarks.multimodal_quality_eval import (
    evaluate_dataset,
    interval_iou,
)


ROOT = Path(__file__).resolve().parents[1]


def test_interval_iou():
    assert interval_iou(0.0, 10.0, 5.0, 15.0) == 5.0 / 15.0
    assert interval_iou(0.0, 1.0, 2.0, 3.0) == 0.0
    assert interval_iou(None, None, 2.0, 3.0) == 0.0


def test_quality_metrics_cover_recall_temporal_scope_and_contamination():
    seen_assets = []

    async def ranker(query, assets, top_k):
        seen_assets.extend(asset["id"] for asset in assets)
        return {
            "backend": "fixture-semantic",
            "latency_ms": 12.0,
            "debug": {"visual_latency_ms": 7.0, "audio_latency_ms": 3.0},
            "hits": [
                {
                    "asset_id": "video-current",
                    "score": 0.9,
                    "start": 36.0,
                    "end": 44.0,
                },
                {"asset_id": "image-current", "score": 0.8},
            ][:top_k],
        }

    dataset = {
        "name": "unit",
        "cases": [
            {
                "id": "case",
                "query": "find event",
                "assets": [
                    {
                        "id": "video-current",
                        "mime": "video/mp4",
                        "size": 100,
                        "meta": {
                            "kind": "video",
                            "_context": {"scope_eligible": True},
                        },
                    },
                    {
                        "id": "image-current",
                        "mime": "image/png",
                        "size": 50,
                        "meta": {
                            "kind": "image",
                            "_context": {"scope_eligible": True},
                        },
                    },
                    {
                        "id": "wrong-build",
                        "mime": "video/mp4",
                        "size": 999,
                        "meta": {
                            "kind": "video",
                            "_context": {"scope_eligible": False},
                        },
                    },
                ],
                "relevant": [
                    {"asset_id": "video-current", "start": 35.0, "end": 45.0},
                    {"asset_id": "image-current"},
                ],
                "forbidden_asset_ids": ["wrong-build"],
                "top_k": 2,
            }
        ],
    }

    result = asyncio.run(evaluate_dataset(dataset, ranker))

    assert seen_assets == ["video-current", "image-current"]
    assert result["recall_at_k"] == 1.0
    assert result["mrr"] == 1.0
    assert result["temporal_iou"] == 0.8
    assert result["temporal_hit_rate"] == 1.0
    assert result["forbidden_hits"] == 0
    assert result["scope_filtered_assets"] == 1
    assert result["retrieved_media_bytes_at_k"] == 150
    assert result["worker_lane_seconds_proxy"] == 0.01
    assert result["live_semantic_backend_seen"] is True


def test_multimodal_quality_protocol_smoke_cli():
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "multimodal_quality_benchmark.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    payload = json.loads(completed.stdout)
    assert payload["benchmark"] == "lingjing-multimodal-quality-v1"
    assert payload["evidence_class"] == "synthetic-protocol-smoke-not-quality-evidence"
    assert payload["mode"] == "product-lexical-fallback"
    assert payload["quality_claim"] == "none-protocol-smoke"
    assert payload["forbidden_hits"] == 0
