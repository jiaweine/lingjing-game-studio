from __future__ import annotations

from pathlib import Path

from worldforge.benchmarks.multimodal_annotation_readiness import (
    annotation_readiness_report,
)
from worldforge.benchmarks.multimodal_workspace import new_workspace


def test_readiness_preflights_temporal_bounds_before_strict_freeze(tmp_path: Path):
    video_path = tmp_path / "assets" / "run.mp4"
    video_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(b"video fixture")
    workspace = new_workspace(tmp_path)
    asset = workspace["asset_catalog"][0]
    asset["meta"]["duration"] = 5.0
    workspace["heldout_policy"]["development_excluded"] = True
    workspace["cases"] = [
        {
            "id": "temporal-out-of-range",
            "query": "human-authored temporal question",
            "source_group": "capture-001",
            "target_modalities": ["video"],
            "annotation": {"annotator_count": 2, "adjudicated": True},
            "candidate_asset_ids": [asset["id"]],
            "relevant": [{"asset_id": asset["id"], "start": 4.0, "end": 6.0}],
            "forbidden_asset_ids": [],
        }
    ]

    report = annotation_readiness_report(workspace)

    assert report["coverage"]["annotation_complete"] is True
    assert report["strict_case_semantic_preflight"]["ran"] is True
    assert report["coverage"]["case_semantics_valid"] is False
    assert any(
        "interval exceeds asset duration" in error
        for error in report["strict_case_semantic_preflight"]["errors"]
    )
    assert report["coverage"]["ready_for_strict_freeze_attempt"] is False
