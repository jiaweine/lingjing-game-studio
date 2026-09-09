from __future__ import annotations

from pathlib import Path

from worldforge.benchmarks.multimodal_annotation_readiness import (
    annotation_readiness_report,
)
from worldforge.benchmarks.multimodal_workspace import new_workspace


def test_annotation_can_be_complete_before_heldout_split_is_sealed(tmp_path: Path):
    asset_path = tmp_path / "assets" / "note.txt"
    asset_path.parent.mkdir(parents=True, exist_ok=True)
    asset_path.write_text("human evidence", encoding="utf-8")
    workspace = new_workspace(tmp_path)
    asset = workspace["asset_catalog"][0]
    workspace["cases"] = [
        {
            "id": "case-001",
            "query": "human-authored retrieval question",
            "source_group": "capture-001",
            "target_modalities": ["text"],
            "annotation": {"annotator_count": 2, "adjudicated": True},
            "candidate_asset_ids": [asset["id"]],
            "relevant": [{"asset_id": asset["id"]}],
            "forbidden_asset_ids": [],
        }
    ]
    assert workspace["heldout_policy"]["development_excluded"] is False

    report = annotation_readiness_report(workspace)

    assert report["coverage"]["annotation_complete"] is True
    assert report["coverage"]["development_excluded"] is False
    assert report["workspace_validation"]["authoring_freeze_ready"] is False
    assert report["coverage"]["ready_for_strict_freeze_attempt"] is False
