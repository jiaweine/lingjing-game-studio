from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.multimodal_quality_matrix import metric_deltas, parse_backend_spec


ROOT = Path(__file__).resolve().parents[1]


def test_parse_backend_spec():
    assert parse_backend_spec("wemm=http://127.0.0.1:8910/") == (
        "wemm",
        "http://127.0.0.1:8910",
    )
    with pytest.raises(argparse.ArgumentTypeError):
        parse_backend_spec("missing-url")
    with pytest.raises(argparse.ArgumentTypeError):
        parse_backend_spec("baseline=http://127.0.0.1:8910")


def test_metric_deltas_keep_unavailable_temporal_metrics_explicit():
    baseline = {
        "recall_at_k": 0.5,
        "mrr": 0.4,
        "temporal_iou": None,
        "temporal_hit_rate": None,
        "build_contamination_rate": 0.0,
        "retrieved_media_bytes_at_k": 100,
        "latency_p50_ms": 2.0,
        "latency_p95_ms": 3.0,
        "worker_lane_seconds_proxy": 0.0,
    }
    current = {
        **baseline,
        "recall_at_k": 0.75,
        "mrr": 0.6,
        "latency_p95_ms": 13.0,
    }
    delta = metric_deltas(current, baseline)
    assert delta["recall_at_k"] == 0.25
    assert delta["mrr"] == pytest.approx(0.2)
    assert delta["latency_p95_ms"] == 10.0
    assert delta["temporal_iou"] is None


def test_multimodal_quality_matrix_protocol_smoke_cli():
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "multimodal_quality_matrix.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    payload = json.loads(completed.stdout)
    assert payload["benchmark"] == "lingjing-multimodal-quality-matrix-v1"
    assert payload["backend_order"] == ["baseline"]
    assert payload["quality_claim"] == "none-protocol-smoke"
    assert payload["runs"][0]["forbidden_hits"] == 0
