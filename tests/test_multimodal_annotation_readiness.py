from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from worldforge.benchmarks.multimodal_annotation_readiness import (
    annotation_readiness_report,
)
from worldforge.benchmarks.multimodal_workspace import new_workspace


ROOT = Path(__file__).resolve().parents[1]


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _workspace(tmp_path: Path) -> dict:
    _write(tmp_path / "assets" / "notes.txt", b"notes")
    _write(tmp_path / "assets" / "frame.png", b"frame")
    _write(tmp_path / "assets" / "run.mp4", b"video")
    _write(tmp_path / "assets" / "sound.wav", b"audio")
    _write(tmp_path / "assets" / "wrong.mp4", b"wrong")
    return new_workspace(tmp_path)


def _asset(workspace: dict, kind: str, name: str | None = None) -> dict:
    for row in workspace["asset_catalog"]:
        if (row.get("meta") or {}).get("kind") != kind:
            continue
        if name is not None and row.get("name") != name:
            continue
        return row
    raise AssertionError((kind, name))


def _fill_protocol(workspace: dict) -> dict:
    workspace["heldout_policy"]["development_excluded"] = True
    text = _asset(workspace, "text")
    image = _asset(workspace, "image")
    video = _asset(workspace, "video", "run.mp4")
    audio = _asset(workspace, "audio")
    wrong = _asset(workspace, "video", "wrong.mp4")
    video["meta"]["duration"] = 30.0
    targets = [text, image, video, audio]
    cases = []
    for index in range(100):
        target = targets[index % 4]
        kind = target["meta"]["kind"]
        relevant = {"asset_id": target["id"]}
        if kind == "video":
            relevant.update({"start": 10.0, "end": 20.0})
        candidates = [target["id"]]
        forbidden = []
        if index < 25:
            candidates.append(wrong["id"])
            forbidden.append(wrong["id"])
        cases.append(
            {
                "id": f"case-{index:03d}",
                "query": f"human query {index}",
                "source_group": f"capture-{index % 20:02d}",
                "target_modalities": [kind],
                "annotation": {"annotator_count": 2, "adjudicated": True},
                "candidate_asset_ids": candidates,
                "relevant": [relevant],
                "forbidden_asset_ids": forbidden,
                "top_k": 5,
            }
        )
    workspace["cases"] = cases
    return workspace


def test_empty_workspace_reports_exact_protocol_deficits_without_inventing_work(tmp_path: Path):
    workspace = _workspace(tmp_path)
    report = annotation_readiness_report(workspace)

    assert report["evidence_claim"] == "none-annotation-readiness-audit"
    assert report["cases"]["total"] == 0
    assert report["cases"]["fully_annotated"] == 0
    assert report["coverage"]["annotation_complete"] is False
    assert report["coverage"]["protocol_coverage_ready"] is False
    assert report["coverage"]["ready_for_strict_freeze_attempt"] is False
    assert report["coverage"]["deficits"] == {
        "cases": 100,
        "unique_source_groups": 20,
        "target_modalities": {"audio": 20, "image": 20, "text": 20, "video": 20},
        "temporal_cases": 20,
        "scope_negative_cases": 25,
    }


def test_full_human_protocol_reports_coverage_without_claiming_model_quality(tmp_path: Path):
    workspace = _fill_protocol(_workspace(tmp_path))
    report = annotation_readiness_report(workspace)

    assert report["workspace_validation"]["structurally_valid"] is True
    assert report["workspace_validation"]["authoring_freeze_ready"] is True
    assert report["coverage"]["annotation_complete"] is True
    assert report["coverage"]["protocol_coverage_ready"] is True
    assert report["coverage"]["ready_for_strict_freeze_attempt"] is True
    assert report["cases"]["total"] == 100
    assert report["cases"]["fully_annotated"] == 100
    assert report["cases"]["unique_source_groups"] == 20
    assert report["cases"]["target_modality_cases"] == {
        "audio": 25,
        "image": 25,
        "text": 25,
        "video": 25,
    }
    assert report["cases"]["temporal_cases"] == 25
    assert report["cases"]["scope_negative_cases"] == 25
    assert report["evidence_claim"] == "none-annotation-readiness-audit"
    assert "not model-quality evidence" in report["readiness_semantics"]


def test_incomplete_case_breaks_annotation_completion_but_not_aggregate_coverage(tmp_path: Path):
    workspace = _fill_protocol(_workspace(tmp_path))
    workspace["cases"][0]["annotation"] = {"annotator_count": 1, "adjudicated": False}
    report = annotation_readiness_report(workspace)

    assert report["coverage"]["protocol_coverage_ready"] is True
    assert report["coverage"]["annotation_complete"] is False
    assert report["coverage"]["ready_for_strict_freeze_attempt"] is False
    assert report["cases"]["incomplete"] == 1
    assert report["cases"]["missing_field_counts"]["annotator_count"] == 1
    assert report["cases"]["missing_field_counts"]["adjudicated"] == 1


def test_duplicate_content_across_source_groups_is_audit_warning_not_auto_relabel(tmp_path: Path):
    _write(tmp_path / "assets" / "a.txt", b"identical")
    _write(tmp_path / "assets" / "b.txt", b"identical")
    workspace = new_workspace(tmp_path)
    a, b = workspace["asset_catalog"]
    workspace["heldout_policy"]["development_excluded"] = True
    workspace["cases"] = [
        {
            "id": "case-a",
            "query": "human a",
            "source_group": "capture-a",
            "target_modalities": ["text"],
            "annotation": {"annotator_count": 2, "adjudicated": True},
            "candidate_asset_ids": [a["id"]],
            "relevant": [{"asset_id": a["id"]}],
            "forbidden_asset_ids": [],
        },
        {
            "id": "case-b",
            "query": "human b",
            "source_group": "capture-b",
            "target_modalities": ["text"],
            "annotation": {"annotator_count": 2, "adjudicated": True},
            "candidate_asset_ids": [b["id"]],
            "relevant": [{"asset_id": b["id"]}],
            "forbidden_asset_ids": [],
        },
    ]

    report = annotation_readiness_report(workspace)
    assert len(report["assets"]["duplicate_content_groups"]) == 1
    assert len(report["leakage_audit"]["cross_source_group_duplicate_content"]) == 1
    assert report["leakage_audit"]["automatic_block"] is False
    assert {case["source_group"] for case in workspace["cases"]} == {
        "capture-a",
        "capture-b",
    }


def test_scope_distribution_surfaces_declared_alias_conflict_for_human_review(tmp_path: Path):
    workspace = _workspace(tmp_path)
    asset = workspace["asset_catalog"][0]
    asset["meta"].update({"build_ref": "1.4.7", "version": "1.4.8"})
    report = annotation_readiness_report(workspace)

    conflicts = report["assets"]["scope_alias_conflicts"]
    assert conflicts == [
        {"asset_id": asset["id"], "field": "build_ref", "values": ["1.4.7", "1.4.8"]}
    ]
    assert any(
        warning["code"] == "asset-scope-alias-conflict"
        for warning in report["leakage_audit"]["warnings"]
    )


def test_readiness_cli_emits_report_and_requirement_exit_codes(tmp_path: Path):
    workspace = _workspace(tmp_path)
    workspace_path = tmp_path / "workspace.json"
    workspace_path.write_text(json.dumps(workspace), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "multimodal_annotation_readiness.py"),
            "--workspace",
            str(workspace_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    report = json.loads(completed.stdout)
    assert report["coverage"]["deficits"]["cases"] == 100

    gated = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "multimodal_annotation_readiness.py"),
            "--workspace",
            str(workspace_path),
            "--require-ready-for-freeze",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert gated.returncode == 4
