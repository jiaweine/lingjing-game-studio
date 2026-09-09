from __future__ import annotations

import hashlib

import pytest

from worldforge.benchmarks.multimodal_corpus import (
    QUALITY_PROTOCOL,
    resolve_dataset_paths,
    validate_corpus,
)


def _dataset(path: str, sha256: str) -> dict:
    return {
        "name": QUALITY_PROTOCOL["name"],
        "protocol_version": QUALITY_PROTOCOL["protocol_version"],
        "annotation_guideline_version": QUALITY_PROTOCOL["annotation_guideline_version"],
        "evidence_class": "human-annotated-heldout",
        "split": "heldout",
        "frozen": True,
        "frozen_at": "2026-09-09T00:00:00Z",
        "heldout_policy": {"development_excluded": True},
        "cases": [
            {
                "id": "escape-case",
                "query": "inspect asset",
                "source_group": "source-1",
                "target_modalities": ["text"],
                "annotation": {"annotator_count": 2, "adjudicated": True},
                "assets": [
                    {
                        "id": "asset-1",
                        "name": "outside.txt",
                        "mime": "text/plain",
                        "path": path,
                        "sha256": sha256,
                        "meta": {"kind": "text"},
                    }
                ],
                "relevant": [{"asset_id": "asset-1"}],
                "forbidden_asset_ids": [],
            }
        ],
    }


def test_parent_traversal_cannot_verify_file_outside_corpus(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside evidence", encoding="utf-8")
    sha = hashlib.sha256(outside.read_bytes()).hexdigest()
    dataset = _dataset("../outside.txt", sha)

    report = validate_corpus(dataset, base_dir=corpus, verify_files=True)

    assert report["structurally_valid"] is False
    assert report["files_verified"] is False
    assert any("path must stay inside the corpus root" in row for row in report["errors"])
    with pytest.raises(ValueError, match="corpus-relative"):
        resolve_dataset_paths(dataset, base_dir=corpus)


def test_symlink_cannot_escape_corpus_root(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside evidence", encoding="utf-8")
    sha = hashlib.sha256(outside.read_bytes()).hexdigest()
    (corpus / "assets").mkdir()
    link = corpus / "assets" / "escape.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are unavailable on this platform")
    dataset = _dataset("assets/escape.txt", sha)

    report = validate_corpus(dataset, base_dir=corpus, verify_files=True)

    assert report["structurally_valid"] is False
    assert report["files_verified"] is False
    assert any("path resolves outside the corpus root" in row for row in report["errors"])
    with pytest.raises(ValueError, match="outside corpus root"):
        resolve_dataset_paths(dataset, base_dir=corpus)
