from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from worldforge.benchmarks.multimodal_workspace import (
    compile_workspace,
    freeze_workspace,
    new_workspace,
    validate_workspace,
)


ROOT = Path(__file__).resolve().parents[1]


def _write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _workspace_fixture(tmp_path: Path) -> dict:
    assets = tmp_path / "assets"
    _write(assets / "notes.txt", b"text evidence")
    _write(assets / "frame.png", b"image evidence")
    _write(assets / "run.mp4", b"video evidence")
    _write(assets / "sound.wav", b"audio evidence")
    _write(assets / "wrong.mp4", b"wrong build")
    return new_workspace(tmp_path)


def _asset_by_kind(workspace: dict, kind: str, *, name: str | None = None) -> dict:
    for asset in workspace["asset_catalog"]:
        if (asset.get("meta") or {}).get("kind") != kind:
            continue
        if name is not None and asset.get("name") != name:
            continue
        return asset
    raise AssertionError((kind, name))


def test_scaffold_hashes_real_files_without_inventing_labels(tmp_path: Path):
    assets = tmp_path / "assets"
    _write(assets / "a.txt", b"same")
    _write(assets / "b.txt", b"same")
    _write(assets / "frame.png", b"image")
    _write(assets / "run.mp4", b"video")
    _write(assets / "sound.wav", b"audio")
    _write(assets / "ignored.bin", b"not benchmark media")

    workspace = new_workspace(tmp_path)
    report = workspace["asset_catalog_report"]

    assert report["assets"] == 5
    assert report["modality_counts"] == {
        "audio": 1,
        "image": 1,
        "text": 2,
        "video": 1,
    }
    assert len(report["duplicate_content_groups"]) == 1
    assert set(report["duplicate_content_groups"][0]["paths"]) == {
        "assets/a.txt",
        "assets/b.txt",
    }
    a = next(row for row in workspace["asset_catalog"] if row["name"] == "a.txt")
    b = next(row for row in workspace["asset_catalog"] if row["name"] == "b.txt")
    assert a["sha256"] == hashlib.sha256(b"same").hexdigest()
    assert b["sha256"] == a["sha256"]
    assert a["id"] != b["id"]
    assert workspace["cases"] == []
    assert workspace["heldout_policy"]["development_excluded"] is False
    assert validate_workspace(workspace)["freeze_ready"] is False


def test_compile_materializes_scope_authority_from_human_case_labels(tmp_path: Path):
    workspace = _workspace_fixture(tmp_path)
    image = _asset_by_kind(workspace, "image")
    wrong = _asset_by_kind(workspace, "video", name="wrong.mp4")
    workspace["heldout_policy"]["development_excluded"] = True
    workspace["cases"] = [
        {
            "id": "case-001",
            "query": "find the release frame",
            "source_group": "session-001",
            "target_modalities": ["image"],
            "annotation": {"annotator_count": 2, "adjudicated": True},
            "candidate_asset_ids": [image["id"], wrong["id"]],
            "relevant": [{"asset_id": image["id"]}],
            "forbidden_asset_ids": [wrong["id"]],
            "top_k": 2,
        }
    ]

    compiled = compile_workspace(workspace, freeze=False)
    case = compiled["cases"][0]
    by_id = {row["id"]: row for row in case["assets"]}

    assert by_id[image["id"]]["meta"]["_context"]["scope_eligible"] is True
    assert by_id[wrong["id"]]["meta"]["_context"]["scope_eligible"] is False
    assert case["forbidden_asset_ids"] == [wrong["id"]]
    assert compiled["evidence_class"] == "annotation-in-progress"
    assert compiled["frozen"] is False


def test_freeze_rejects_incomplete_or_unverified_human_work(tmp_path: Path):
    workspace = _workspace_fixture(tmp_path)

    with pytest.raises(ValueError, match="not freeze-ready"):
        freeze_workspace(
            workspace,
            corpus_root=tmp_path,
            frozen_at="2026-09-07T10:00:00Z",
        )


def test_freeze_produces_strict_manifest_only_after_full_protocol(tmp_path: Path):
    workspace = _workspace_fixture(tmp_path)
    workspace["heldout_policy"] = {
        "development_excluded": True,
        "notes": "source-group holdout assignment reviewed before retrieval tuning",
    }
    text = _asset_by_kind(workspace, "text")
    image = _asset_by_kind(workspace, "image")
    video = _asset_by_kind(workspace, "video", name="run.mp4")
    audio = _asset_by_kind(workspace, "audio")
    wrong = _asset_by_kind(workspace, "video", name="wrong.mp4")
    targets = [text, image, video, audio]

    cases = []
    for index in range(100):
        target = targets[index % 4]
        kind = target["meta"]["kind"]
        relevant = {"asset_id": target["id"]}
        if kind == "video":
            relevant.update({"start": 10.0, "end": 20.0})
        candidate_ids = [target["id"]]
        forbidden_ids = []
        if index < 25:
            candidate_ids.append(wrong["id"])
            forbidden_ids.append(wrong["id"])
        cases.append(
            {
                "id": f"case-{index:03d}",
                "query": f"human authored query {index}",
                "source_group": f"capture-{index % 20:02d}",
                "target_modalities": [kind],
                "annotation": {"annotator_count": 2, "adjudicated": True},
                "candidate_asset_ids": candidate_ids,
                "relevant": [relevant],
                "forbidden_asset_ids": forbidden_ids,
                "top_k": 5,
            }
        )
    workspace["cases"] = cases

    manifest, report = freeze_workspace(
        workspace,
        corpus_root=tmp_path,
        frozen_at="2026-09-07T10:00:00Z",
    )

    assert report["strict_quality_eligible"] is True
    assert report["files_verified"] is True
    assert report["cases"] == 100
    assert report["unique_source_groups"] == 20
    assert report["target_modality_cases"] == {
        "audio": 25,
        "image": 25,
        "text": 25,
        "video": 25,
    }
    assert report["temporal_cases"] == 25
    assert report["scope_negative_cases"] == 25
    assert manifest["frozen"] is True
    assert manifest["evidence_class"] == "human-annotated-heldout"
    assert manifest["split"] == "heldout"
    assert manifest["corpus_digest"] == report["corpus_digest"]


def test_scaffold_cli_writes_only_inventory_and_empty_annotation_workspace(tmp_path: Path):
    _write(tmp_path / "assets" / "capture.png", b"capture")
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "scaffold_multimodal_corpus.py"),
            "--corpus-root",
            str(tmp_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    report = json.loads(completed.stdout)
    workspace = json.loads((tmp_path / "workspace.json").read_text(encoding="utf-8"))
    assert report["evidence_claim"] == "none-annotation-workspace"
    assert workspace["cases"] == []
    assert workspace["asset_catalog"][0]["path"] == "assets/capture.png"
