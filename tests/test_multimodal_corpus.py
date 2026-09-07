from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

from worldforge.benchmarks.multimodal_corpus import validate_corpus


ROOT = Path(__file__).resolve().parents[1]


def _write(path: Path, content: bytes) -> str:
    path.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


def _strict_dataset(tmp_path: Path) -> dict:
    files = {
        "text": ("text.txt", "text/plain", b"text evidence"),
        "image": ("image.png", "image/png", b"image evidence"),
        "video": ("video.mp4", "video/mp4", b"video evidence"),
        "audio": ("audio.wav", "audio/wav", b"audio evidence"),
        "wrong": ("wrong.mp4", "video/mp4", b"wrong build evidence"),
    }
    shas = {
        key: _write(tmp_path / filename, content)
        for key, (filename, _mime, content) in files.items()
    }

    modalities = ["text", "image", "video", "audio"]
    cases = []
    for index in range(100):
        modality = modalities[index % len(modalities)]
        filename, mime, _content = files[modality]
        relevant = {"asset_id": f"{modality}-asset"}
        meta = {
            "kind": modality,
            "_context": {"scope_eligible": True},
        }
        if modality == "video":
            meta["duration"] = 120.0
            relevant.update({"start": 40.0, "end": 50.0})
        assets = [
            {
                "id": f"{modality}-asset",
                "name": filename,
                "mime": mime,
                "path": filename,
                "sha256": shas[modality],
                "meta": meta,
            }
        ]
        forbidden = []
        if index < 25:
            assets.append(
                {
                    "id": "wrong-build",
                    "name": files["wrong"][0],
                    "mime": files["wrong"][1],
                    "path": files["wrong"][0],
                    "sha256": shas["wrong"],
                    "meta": {
                        "kind": "video",
                        "_context": {"scope_eligible": False},
                    },
                }
            )
            forbidden = ["wrong-build"]
        cases.append(
            {
                "id": f"case-{index:03d}",
                "query": f"find {modality} evidence {index}",
                "source_group": f"source-{index % 20:02d}",
                "target_modalities": [modality],
                "annotation": {"annotator_count": 2, "adjudicated": True},
                "assets": assets,
                "relevant": [relevant],
                "forbidden_asset_ids": forbidden,
                "top_k": 5,
            }
        )

    return {
        "name": "game-rd-mm-v1",
        "protocol_version": "1.0",
        "annotation_guideline_version": "game-rd-mm-v1-annotation-1",
        "evidence_class": "human-annotated-heldout",
        "split": "heldout",
        "frozen": True,
        "frozen_at": "2026-09-07T00:00:00Z",
        "heldout_policy": {"development_excluded": True},
        "cases": cases,
    }


def test_strict_corpus_requires_verified_content_hashes(tmp_path: Path):
    dataset = _strict_dataset(tmp_path)

    unverified = validate_corpus(dataset, base_dir=tmp_path, verify_files=False)
    assert unverified["structurally_valid"] is True
    assert unverified["strict_quality_eligible"] is False
    assert "file hash verification" in " ".join(unverified["quality_blockers"])

    verified = validate_corpus(dataset, base_dir=tmp_path, verify_files=True)
    assert verified["structurally_valid"] is True
    assert verified["strict_quality_eligible"] is True
    assert verified["files_verified"] is True
    assert verified["cases"] == 100
    assert verified["unique_source_groups"] == 20
    assert verified["target_modality_cases"] == {
        "audio": 25,
        "image": 25,
        "text": 25,
        "video": 25,
    }
    assert verified["temporal_cases"] == 25
    assert verified["scope_negative_cases"] == 25


def test_relevant_asset_cannot_also_be_forbidden(tmp_path: Path):
    dataset = _strict_dataset(tmp_path)
    first = dataset["cases"][0]
    first["forbidden_asset_ids"] = [first["relevant"][0]["asset_id"]]

    report = validate_corpus(dataset, base_dir=tmp_path, verify_files=True)

    assert report["structurally_valid"] is False
    assert any("relevant/forbidden overlap" in row for row in report["errors"])


def test_protocol_json_matches_validator_contract():
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "validate_multimodal_corpus.py"),
            "--protocol-only",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    payload = json.loads(completed.stdout)
    assert payload == {
        "protocol": "game-rd-mm-v1",
        "protocol_version": "1.0",
        "protocol_matches_code": True,
    }
