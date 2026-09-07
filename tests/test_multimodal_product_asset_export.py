from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from worldforge.benchmarks.multimodal_product_export import (
    attach_source_inventory,
    stage_product_assets,
)
from worldforge.benchmarks.multimodal_workspace import new_workspace
from worldforge.product.store import ConversationStore, DEMO_WORKSPACE_ID
from worldforge.storage import LocalObjectStorage


ROOT = Path(__file__).resolve().parents[1]


def _fixture(tmp_path: Path):
    store = ConversationStore(
        tmp_path / "product.db",
        tmp_path / "legacy-assets",
        seed_dev_identity=True,
    )
    conversation = store.create_conversation(
        "Boss regression capture",
        workspace_id=DEMO_WORKSPACE_ID,
    )
    storage = LocalObjectStorage(tmp_path / "objects")
    payload = b"build=1.4.7\nphase2 stun regression\n"
    object_key = f"{DEMO_WORKSPACE_ID}/assets/source/trace.log"
    storage.put_bytes(object_key, payload, "text/plain")
    asset = store.add_asset(
        conversation["id"],
        name="boss-trace.log",
        mime="text/plain",
        path=object_key,
        size=len(payload),
        meta={
            "kind": "text",
            "chars": len(payload),
            "lines": 2,
            "build_ref": "1.4.7",
            "branch_ref": "release/1.4",
            "commit_ref": "abc123",
            "environment_ref": "qa",
            "preview": "must not be copied into objective benchmark metadata",
        },
        workspace_id=DEMO_WORKSPACE_ID,
        storage_backend="local",
    )
    return store, storage, conversation, asset, payload


def test_product_export_stages_bytes_without_creating_labels_and_preserves_provenance(tmp_path: Path):
    store, storage, conversation, asset, payload = _fixture(tmp_path)
    corpus_root = tmp_path / "corpus"

    inventory, report = stage_product_assets(
        store,
        storage,
        corpus_root=corpus_root,
        workspace_id=DEMO_WORKSPACE_ID,
        conversation_ids=[conversation["id"]],
    )

    assert report["selected_product_assets"] == 1
    assert report["staged_assets"] == 1
    assert report["annotation_labels_emitted"] is False
    assert report["evidence_claim"] == "none-source-staging-only"
    assert inventory["annotation_labels_emitted"] is False
    assert "cases" not in inventory
    source = inventory["assets"][0]
    assert source["source_asset_id"] == asset["id"]
    assert source["source_conversation_id"] == conversation["id"]
    assert source["objective_meta"]["build_ref"] == "1.4.7"
    assert "preview" not in source["objective_meta"]
    assert (corpus_root / source["staged_path"]).read_bytes() == payload

    workspace = attach_source_inventory(
        new_workspace(corpus_root),
        corpus_root=corpus_root,
    )
    assert workspace["cases"] == []
    catalog_asset = workspace["asset_catalog"][0]
    assert catalog_asset["meta"]["build_ref"] == "1.4.7"
    assert catalog_asset["meta"]["branch_ref"] == "release/1.4"
    assert "_context" not in catalog_asset["meta"]
    provenance = catalog_asset["meta"]["source_provenance"]
    assert provenance["source_asset_id"] == asset["id"]
    assert provenance["source_conversation_id"] == conversation["id"]
    assert provenance["source_inventory_digest"] == inventory["source_inventory_digest"]


def test_scaffold_rejects_managed_export_file_hash_drift(tmp_path: Path):
    store, storage, conversation, _asset, _payload = _fixture(tmp_path)
    corpus_root = tmp_path / "corpus"
    inventory, _report = stage_product_assets(
        store,
        storage,
        corpus_root=corpus_root,
        workspace_id=DEMO_WORKSPACE_ID,
        conversation_ids=[conversation["id"]],
    )
    staged = corpus_root / inventory["assets"][0]["staged_path"]
    staged.write_bytes(b"tampered after export")

    with pytest.raises(ValueError, match="hash drift"):
        attach_source_inventory(new_workspace(corpus_root), corpus_root=corpus_root)


def test_export_refuses_storage_backend_mismatch(tmp_path: Path):
    store, _storage, conversation, _asset, _payload = _fixture(tmp_path)
    object_key = f"{DEMO_WORKSPACE_ID}/assets/source/remote.wav"
    store.add_asset(
        conversation["id"],
        name="remote.wav",
        mime="audio/wav",
        path=object_key,
        size=4,
        meta={"kind": "audio"},
        workspace_id=DEMO_WORKSPACE_ID,
        storage_backend="s3",
    )
    local_storage = LocalObjectStorage(tmp_path / "objects")

    with pytest.raises(ValueError, match="storage backend"):
        stage_product_assets(
            store,
            local_storage,
            corpus_root=tmp_path / "corpus",
            workspace_id=DEMO_WORKSPACE_ID,
            conversation_ids=[conversation["id"]],
        )


def test_export_cli_and_scaffold_cli_form_unlabeled_staging_pipeline(tmp_path: Path):
    store, _storage, conversation, asset, _payload = _fixture(tmp_path)
    corpus_root = tmp_path / "corpus"
    database_url = f"sqlite:///{(tmp_path / 'product.db').as_posix()}"

    exported = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "export_multimodal_product_assets.py"),
            "--corpus-root",
            str(corpus_root),
            "--workspace-id",
            DEMO_WORKSPACE_ID,
            "--conversation-id",
            conversation["id"],
            "--database-url",
            database_url,
            "--storage-backend",
            "local",
            "--object-root",
            str(tmp_path / "objects"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert exported.returncode == 0, exported.stderr or exported.stdout
    export_report = json.loads(exported.stdout)
    assert export_report["annotation_labels_emitted"] is False

    scaffolded = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "scaffold_multimodal_corpus.py"),
            "--corpus-root",
            str(corpus_root),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert scaffolded.returncode == 0, scaffolded.stderr or scaffolded.stdout
    workspace = json.loads((corpus_root / "workspace.json").read_text(encoding="utf-8"))
    assert workspace["cases"] == []
    assert workspace["heldout_policy"]["development_excluded"] is False
    assert workspace["source_inventory"]["annotation_labels_emitted"] is False
    staged = workspace["asset_catalog"][0]
    assert staged["meta"]["source_provenance"]["source_asset_id"] == asset["id"]
