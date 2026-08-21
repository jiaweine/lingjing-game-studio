from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy import inspect

from worldforge.product.store import ConversationStore


def test_legacy_auto_created_database_gets_development_lease_columns(tmp_path):
    database = tmp_path / "legacy.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE analysis_jobs (
                id VARCHAR(64) PRIMARY KEY,
                workspace_id VARCHAR(64) NOT NULL,
                conversation_id VARCHAR(64) NOT NULL,
                status VARCHAR(32) NOT NULL,
                payload TEXT NOT NULL,
                attempts INTEGER NOT NULL,
                worker_id VARCHAR(96),
                last_error TEXT,
                created_at FLOAT NOT NULL,
                available_at FLOAT NOT NULL,
                claimed_at FLOAT,
                completed_at FLOAT
            )
            """
        )

    store = ConversationStore(database, tmp_path / "assets")
    schema = inspect(store.engine)
    columns = {column["name"] for column in schema.get_columns("analysis_jobs")}
    indexes = {index["name"] for index in schema.get_indexes("analysis_jobs")}

    assert {"lease_token", "heartbeat_at", "lease_expires_at"} <= columns
    assert "ix_analysis_jobs_lease_expires_at" in indexes


def _queued_job(tmp_path):
    store = ConversationStore(tmp_path / "jobs.db", tmp_path / "assets")
    conversation = store.create_conversation("Lease recovery")
    job = store.enqueue_job(
        workspace_id=conversation["workspace_id"],
        conversation_id=conversation["id"],
        payload={"text": "reproduce the issue"},
    )
    return store, conversation, job


def test_lease_heartbeat_recovery_and_fencing(tmp_path):
    store, conversation, job = _queued_job(tmp_path)
    first = store.claim_job("worker-a", job_id=job["id"], lease_seconds=10)

    assert first is not None
    assert first["status"] == "running"
    assert first["attempts"] == 1
    assert first["lease_token"]
    assert "lease_token" not in store.get_job(
        job["id"], workspace_id=conversation["workspace_id"]
    )
    assert store.claim_job("worker-b", job_id=job["id"]) is None
    with pytest.raises(ValueError, match="lease token"):
        store.complete_job_answer(
            job["id"],
            workspace_id=conversation["workspace_id"],
            content="unfenced result",
            payload={},
            lease_token="",
        )

    heartbeat_at = first["heartbeat_at"] + 2
    assert not store.heartbeat_job(
        job["id"],
        worker_id="worker-b",
        lease_token=first["lease_token"],
        lease_seconds=10,
        now=heartbeat_at,
    )
    assert store.heartbeat_job(
        job["id"],
        worker_id="worker-a",
        lease_token=first["lease_token"],
        lease_seconds=10,
        now=heartbeat_at,
    )

    renewed = store.get_job(job["id"], workspace_id=conversation["workspace_id"])
    assert renewed["heartbeat_at"] == heartbeat_at
    assert renewed["lease_expires_at"] == heartbeat_at + 10

    reclaim_at = renewed["lease_expires_at"] + 1
    assert store.requeue_expired_jobs(now=reclaim_at) == 1
    assert store.get_job(
        job["id"], workspace_id=conversation["workspace_id"]
    )["status"] == "queued"

    second = store.claim_job(
        "worker-b",
        job_id=job["id"],
        lease_seconds=10,
        now=reclaim_at,
    )
    assert second is not None
    assert second["attempts"] == 2
    assert second["lease_token"] != first["lease_token"]

    # The recovered worker cannot commit or fail the newer worker's attempt.
    assert store.complete_job_answer(
        job["id"],
        workspace_id=conversation["workspace_id"],
        content="stale result",
        payload={},
        lease_token=first["lease_token"],
    ) is None
    stale_failure = store.fail_job(
        job["id"],
        "stale worker failed",
        lease_token=first["lease_token"],
    )
    assert stale_failure and stale_failure["status"] == "running"

    message = store.complete_job_answer(
        job["id"],
        workspace_id=conversation["workspace_id"],
        content="fresh result",
        payload={"evidence": []},
        lease_token=second["lease_token"],
    )
    assert message and message["content"] == "fresh result"
    messages = store.list_messages(
        conversation["id"], workspace_id=conversation["workspace_id"]
    )
    assert [item["content"] for item in messages] == ["fresh result"]


def test_cancel_revokes_lease_and_rejects_late_worker(tmp_path):
    store, conversation, job = _queued_job(tmp_path)
    claimed = store.claim_job("worker-a", job_id=job["id"])
    assert claimed is not None

    cancelled = store.cancel_job(
        job["id"], workspace_id=conversation["workspace_id"]
    )
    assert cancelled["status"] == "cancelled"
    assert cancelled["lease_expires_at"] is None
    assert not store.heartbeat_job(
        job["id"],
        worker_id="worker-a",
        lease_token=claimed["lease_token"],
    )
    assert store.complete_job_answer(
        job["id"],
        workspace_id=conversation["workspace_id"],
        content="late result",
        payload={},
        lease_token=claimed["lease_token"],
    ) is None
