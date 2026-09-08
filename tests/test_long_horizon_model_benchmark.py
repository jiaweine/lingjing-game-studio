from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from scripts.long_horizon_model_benchmark import protocol_smoke_dataset
from worldforge.benchmarks.long_horizon_model_eval import (
    aggregate_model_scores,
    score_model_response,
    validate_model_dataset,
)


ROOT = Path(__file__).resolve().parents[1]


def test_anchor_rubric_scores_required_forbidden_abstention_and_workflow():
    dataset = protocol_smoke_dataset()
    scores = [
        score_model_response(case, case["fixture_response"])
        for case in dataset["cases"]
    ]
    metrics = aggregate_model_scores(scores)
    assert metrics["case_pass_rate"] == 1.0
    assert metrics["required_anchor_recall"] == 1.0
    assert metrics["forbidden_claim_rate"] == 0.0
    assert metrics["abstention_compliance"] == 1.0
    assert metrics["workflow_order_compliance"] == 1.0


def test_protocol_smoke_is_structural_but_not_quality_eligible():
    report = validate_model_dataset(protocol_smoke_dataset())
    assert report["structurally_valid"] is True
    assert report["strict_quality_eligible"] is False
    assert report["cases"] == 4
    assert any("requires >= 40 cases" in row for row in report["quality_blockers"])


def test_strict_model_dataset_requires_balanced_human_heldout_long_range_cases():
    source = protocol_smoke_dataset()["cases"]
    cases = []
    for index in range(40):
        template = json.loads(json.dumps(source[index % 4]))
        template["id"] = f"case-{index:03d}"
        template.pop("fixture_response", None)
        cases.append(template)
    dataset = {
        "name": "lingjing-long-horizon-model-v1",
        "protocol_version": "1.0",
        "evidence_class": "human-authored-heldout",
        "frozen": True,
        "heldout_policy": {"development_excluded": True},
        "cases": cases,
    }
    report = validate_model_dataset(dataset)
    assert report["structurally_valid"] is True
    assert report["strict_quality_eligible"] is True
    assert report["category_counts"] == {
        "qa": 10,
        "update": 10,
        "abstention": 10,
        "workflow": 10,
    }
    assert report["long_range_cases"] == 40


def test_cli_default_is_explicit_protocol_smoke_not_model_quality():
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "long_horizon_model_benchmark.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    payload = json.loads(completed.stdout)
    assert payload["evidence_class"] == "synthetic-protocol-smoke-not-model-quality-evidence"
    assert payload["quality_claim"] == "none-protocol-smoke"
    assert payload["fixture_scorer"]["case_pass_rate"] == 1.0
