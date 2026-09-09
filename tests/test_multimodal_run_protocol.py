from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from scripts.multimodal_quality_benchmark import _live_run_blockers
from scripts.multimodal_quality_matrix import _matrix_run_blockers, parse_deployment_spec
from worldforge.benchmarks.multimodal_run import (
    aggregate_evaluations,
    attach_run_manifest,
    measured_run_protocol_eligible,
    write_result_json,
)


def _evaluation(
    *,
    recall: float,
    mrr: float,
    temporal: float,
    forbidden_hits: int,
    latencies: tuple[float, float],
    media_bytes: int,
    worker_seconds: float,
    semantic_seen: bool,
) -> dict:
    return {
        "benchmark": "lingjing-multimodal-quality-v1",
        "dataset": "fixture",
        "evidence_class": "fixture",
        "cases": 2,
        "recall_at_k": recall,
        "mrr": mrr,
        "temporal_iou": temporal,
        "temporal_hit_rate": 1.0,
        "build_contamination_rate": 0.0,
        "forbidden_hits": forbidden_hits,
        "scope_filtered_assets": 2,
        "retrieved_media_bytes_at_k": media_bytes,
        "latency_p50_ms": sum(latencies) / 2,
        "latency_p95_ms": max(latencies),
        "worker_lane_seconds_proxy": worker_seconds,
        "backends": {"fixture-semantic": 2},
        "live_semantic_backend_seen": semantic_seen,
        "case_results": [
            {
                "case_id": "a",
                "returned_hits": 1,
                "forbidden_hits": forbidden_hits,
                "latency_ms": latencies[0],
            },
            {
                "case_id": "b",
                "returned_hits": 1,
                "forbidden_hits": 0,
                "latency_ms": latencies[1],
            },
        ],
    }


def test_repeat_aggregation_pools_latency_and_never_selects_best_run():
    first = _evaluation(
        recall=0.5,
        mrr=0.25,
        temporal=0.4,
        forbidden_hits=0,
        latencies=(10.0, 20.0),
        media_bytes=100,
        worker_seconds=0.1,
        semantic_seen=True,
    )
    second = _evaluation(
        recall=1.0,
        mrr=0.75,
        temporal=0.6,
        forbidden_hits=1,
        latencies=(30.0, 40.0),
        media_bytes=200,
        worker_seconds=0.2,
        semantic_seen=False,
    )

    result = aggregate_evaluations([first, second])

    assert result["recall_at_k"] == 0.75
    assert result["mrr"] == 0.5
    assert result["temporal_iou"] == 0.5
    assert result["forbidden_hits"] == 1
    assert result["build_contamination_rate"] == 0.25
    assert result["latency_p50_ms"] == 25.0
    assert result["latency_p95_ms"] == pytest.approx(38.5)
    assert result["retrieved_media_bytes_at_k"] == 150
    assert result["worker_lane_seconds_proxy"] == pytest.approx(0.15)
    assert result["measured_repeats"] == 2
    assert result["live_semantic_backend_seen"] is True
    assert result["live_semantic_backend_seen_all_repeats"] is False
    assert len(result["repeat_metric_samples"]) == 2
    assert result["case_results"] == first["case_results"]


def test_measured_run_protocol_requires_warmup_and_three_measured_repeats():
    assert measured_run_protocol_eligible(measured_repeats=3, warmup_repeats=1)
    assert not measured_run_protocol_eligible(measured_repeats=2, warmup_repeats=1)
    assert not measured_run_protocol_eligible(measured_repeats=3, warmup_repeats=0)


def test_run_manifest_records_revision_digest_and_atomic_json(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LINGJING_BENCH_SOURCE_REVISION", "commit-fixture-123")
    payload = {"benchmark": "fixture", "value": 1}
    attach_run_manifest(
        payload,
        root=tmp_path,
        dataset_digest="d" * 64,
        measured_repeats=3,
        warmup_repeats=1,
        schedule="fixture-schedule",
        deployment_ids={"wemm": "image@sha256:abc-model@rev1"},
    )

    manifest = payload["run_manifest"]
    assert manifest["benchmark_code_revision"] == "commit-fixture-123"
    assert manifest["dataset_digest"] == "d" * 64
    assert manifest["cache_state"] == "warm-after-full-corpus-warmup"
    assert len(manifest["result_digest"]) == 64

    output = tmp_path / "results" / "run.json"
    write_result_json(payload, str(output))
    assert json.loads(output.read_text(encoding="utf-8")) == payload
    assert not output.with_suffix(".json.tmp").exists()


def test_live_quality_gate_requires_deployment_repeats_semantic_and_zero_contamination():
    assert _live_run_blockers(
        endpoint="http://127.0.0.1:8910",
        deployment_id="image@sha256:abc-model@rev1",
        measured_repeats=3,
        warmup_repeats=1,
        semantic_seen_all_repeats=True,
        forbidden_hits=0,
    ) == []
    blockers = _live_run_blockers(
        endpoint="http://127.0.0.1:8910",
        deployment_id="",
        measured_repeats=1,
        warmup_repeats=0,
        semantic_seen_all_repeats=False,
        forbidden_hits=1,
    )
    assert any("deployment id" in row for row in blockers)
    assert any(">=3 measured repeats" in row for row in blockers)
    assert any("semantic backend" in row for row in blockers)
    assert any("contamination" in row for row in blockers)


def test_parse_deployment_spec_keeps_backend_identity_separate_from_endpoint():
    assert parse_deployment_spec("wemm=image@sha256:abc-model@rev1") == (
        "wemm",
        "image@sha256:abc-model@rev1",
    )
    with pytest.raises(argparse.ArgumentTypeError):
        parse_deployment_spec("wemm=")


def test_matrix_quality_gate_rejects_missing_deployment_and_contamination():
    runs = [
        {"label": "baseline", "forbidden_hits": 0},
        {
            "label": "wemm",
            "forbidden_hits": 1,
            "live_semantic_backend_seen_all_repeats": True,
        },
    ]
    blockers = _matrix_run_blockers(
        runs=runs,
        live_labels=["wemm"],
        deployment_ids={"baseline": "baseline", "wemm": ""},
        measured_repeats=3,
        warmup_repeats=1,
    )
    assert any("deployment id missing" in row for row in blockers)
    assert any("contamination" in row for row in blockers)
