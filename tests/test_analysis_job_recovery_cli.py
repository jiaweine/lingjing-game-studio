from __future__ import annotations

import json

from sqlalchemy import update

from scripts.reconcile_analysis_jobs import main
from worldforge.product.store import ConversationStore, DEMO_WORKSPACE_ID


def _stale_database(tmp_path):
    database = tmp_path / "product.db"
    store = ConversationStore(database, tmp_path / "assets")
    conversation = store.create_conversation("recovery cli")
    job = store.enqueue_job(
        workspace_id=DEMO_WORKSPACE_ID,
        conversation_id=conversation["id"],
        payload={"text": "cli recovery", "provider": "auto", "asset_ids": []},
    )
    assert store.claim_job("worker-cli", job_id=job["id"])
    with store.engine.begin() as connection:
        connection.execute(
            update(store.jobs)
            .where(store.jobs.c.id == job["id"])
            .values(claimed_at=1.0)
        )
    return database, store, job


def test_recovery_cli_defaults_to_read_only(tmp_path, capsys):
    database, store, job = _stale_database(tmp_path)
    code = main(
        [
            "--database-url",
            f"sqlite:///{database.as_posix()}",
            "--stale-after-seconds",
            "0",
            "--workspace-id",
            DEMO_WORKSPACE_ID,
        ]
    )
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["mode"] == "dry-run"
    assert report["automatic_replay"] is False
    assert report["candidate_count"] == 1
    assert report["changed_count"] == 0
    assert report["candidates"][0]["id"] == job["id"]
    assert "payload" not in report["candidates"][0]
    assert store.get_job(job["id"], workspace_id=DEMO_WORKSPACE_ID)["status"] == "running"


def test_recovery_cli_requires_apply_to_change_state(tmp_path, capsys):
    database, store, job = _stale_database(tmp_path)
    code = main(
        [
            "--database-url",
            f"sqlite:///{database.as_posix()}",
            "--stale-after-seconds",
            "0",
            "--workspace-id",
            DEMO_WORKSPACE_ID,
            "--apply",
            "--reason",
            "operator marked stale after worker loss",
        ]
    )
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["mode"] == "apply"
    assert report["candidate_count"] == 1
    assert report["changed_count"] == 1
    assert report["changed"][0]["id"] == job["id"]
    recovered = store.get_job(job["id"], workspace_id=DEMO_WORKSPACE_ID)
    assert recovered["status"] == "failed"
    assert recovered["last_error"] == "operator marked stale after worker loss"
