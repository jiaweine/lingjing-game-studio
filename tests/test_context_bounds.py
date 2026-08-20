from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from worldforge.product.store import ConversationStore
from worldforge.settings import load_settings
from worldforge.storage import S3ObjectStorage


def _store(tmp_path):
    store = ConversationStore(
        tmp_path / "product.db",
        tmp_path / "assets",
        seed_dev_identity=False,
    )
    owner = store.create_user_workspace(
        email="context@example.com",
        name="Context Owner",
        password_hash="hashed",
        workspace_name="Context Lab",
    )
    conversation = store.create_conversation(
        "bounded context",
        workspace_id=owner["workspace_id"],
        created_by=owner["user_id"],
    )
    return store, owner, conversation


def test_job_context_keeps_only_recent_history_and_bounded_assets(tmp_path, monkeypatch):
    api_app = importlib.import_module("worldforge.api.app")
    store, owner, conversation = _store(tmp_path)
    monkeypatch.setattr(api_app, "product_store", store)
    monkeypatch.setattr(
        api_app,
        "settings",
        SimpleNamespace(max_context_assets=3, max_context_mb=8),
    )

    for index in range(12):
        store.add_message(
            conversation["id"],
            "user" if index % 2 == 0 else "assistant",
            f"message-{index}",
            workspace_id=owner["workspace_id"],
        )

    assets = []
    for index in range(5):
        assets.append(
            store.add_asset(
                conversation["id"],
                name=f"asset-{index}.txt",
                mime="text/plain",
                path=f"objects/{index}.txt",
                size=1024,
                meta={"kind": "text"},
                workspace_id=owner["workspace_id"],
                created_by=owner["user_id"],
            )
        )

    history, selected, chosen, omitted = api_app._load_job_context(
        conversation["id"],
        owner["workspace_id"],
        [assets[0]["id"]],
    )

    assert [row["content"] for row in history] == [
        f"message-{index}" for index in range(4, 12)
    ]
    assert selected == [assets[0]["id"]]
    assert len(chosen) == 3
    assert assets[0]["id"] in chosen
    assert assets[4]["id"] in chosen
    assert assets[3]["id"] in chosen
    assert omitted == 2


def test_explicit_context_selection_cannot_exceed_count_or_byte_budget(monkeypatch):
    api_app = importlib.import_module("worldforge.api.app")
    rows = [
        {"id": "a", "size": 600_000, "created_at": 1},
        {"id": "b", "size": 600_000, "created_at": 2},
        {"id": "c", "size": 1, "created_at": 3},
    ]

    monkeypatch.setattr(
        api_app,
        "settings",
        SimpleNamespace(max_context_assets=2, max_context_mb=10),
    )
    with pytest.raises(HTTPException) as count_error:
        api_app._select_context_assets(rows, ["a", "b", "c"])
    assert count_error.value.status_code == 413

    monkeypatch.setattr(
        api_app,
        "settings",
        SimpleNamespace(max_context_assets=3, max_context_mb=1),
    )
    with pytest.raises(HTTPException) as byte_error:
        api_app._select_context_assets(rows, ["a", "b"])
    assert byte_error.value.status_code == 413


def test_settings_fail_fast_on_invalid_runtime_modes(monkeypatch):
    monkeypatch.setenv("WORLDFORGE_ENV", "development")
    monkeypatch.setenv("WORLDFORGE_QUEUE_MODE", "externl")
    with pytest.raises(RuntimeError, match="QUEUE_MODE"):
        load_settings()

    monkeypatch.setenv("WORLDFORGE_QUEUE_MODE", "inprocess")
    monkeypatch.setenv("WORLDFORGE_STORAGE_BACKEND", "s33")
    with pytest.raises(RuntimeError, match="STORAGE_BACKEND"):
        load_settings()


def test_production_rejects_short_jwt_secret(monkeypatch):
    monkeypatch.setenv("WORLDFORGE_ENV", "production")
    monkeypatch.setenv("WORLDFORGE_JWT_SECRET", "too-short")
    monkeypatch.setenv("WORLDFORGE_QUEUE_MODE", "external")
    monkeypatch.setenv("WORLDFORGE_STORAGE_BACKEND", "s3")
    with pytest.raises(RuntimeError, match="at least 32"):
        load_settings()


def test_s3_materialize_streams_to_target_without_get_bytes(tmp_path):
    class FakeClient:
        def __init__(self):
            self.calls = []

        def download_file(self, bucket, key, filename):
            self.calls.append((bucket, key, filename))
            Path(filename).write_bytes(b"streamed")

    storage = object.__new__(S3ObjectStorage)
    storage.bucket = "bucket"
    storage.client = FakeClient()
    target = tmp_path / "nested" / "asset.bin"

    result = storage.materialize_to("ws/asset.bin", target)
    assert result == target
    assert target.read_bytes() == b"streamed"
    assert storage.client.calls == [("bucket", "ws/asset.bin", str(target))]
