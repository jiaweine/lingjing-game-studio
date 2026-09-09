from __future__ import annotations

from sqlalchemy import update

from worldforge.product.job_recovery import (
    RECOVERY_REASON,
    fail_stale_running_jobs,
    list_stale_running_jobs,
)
from worldforge.product.store import ConversationStore, DEMO_WORKSPACE_ID


def _running_job(tmp_path):
    store = ConversationStore(tmp_path / "product.db", tmp_path / "assets")
    conversation = store.create_conversation("crash recovery")
    job = store.enqueue_job(
        workspace_id=DEMO_WORKSPACE_ID,
        conversation_id=conversation["id"],
        payload={"text": "recover me", "provider": "auto", "asset_ids": []},
    )
    claimed = store.claim_job("worker-a", job_id=job["id"])
    assert claimed is not None
    assert claimed["status"] == "running"
    with store.engine.begin() as connection:
        connection.execute(
            update(store.jobs)
            .where(store.jobs.c.id == job["id"])
            .values(claimed_at=100.0)
        )
    return store, conversation, job


def test_stale_scan_is_read_only_and_recovery_never_replays_job(tmp_path):
    store, conversation, job = _running_job(tmp_path)

    candidates = list_stale_running_jobs(
        store,
        stale_after_seconds=300,
        now=1000.0,
    )
    assert [row["id"] for row in candidates] == [job["id"]]
    assert store.get_job(job["id"], workspace_id=DEMO_WORKSPACE_ID)["status"] == "running"

    changed = fail_stale_running_jobs(
        store,
        stale_after_seconds=300,
        now=1000.0,
    )
    assert [row["id"] for row in changed] == [job["id"]]

    recovered = store.get_job(job["id"], workspace_id=DEMO_WORKSPACE_ID)
    assert recovered["status"] == "failed"
    assert recovered["attempts"] == 1
    assert recovered["worker_id"] == "worker-a"
    assert recovered["claimed_at"] == 100.0
    assert recovered["completed_at"] == 1000.0
    assert recovered["last_error"] == RECOVERY_REASON
    assert store.get_conversation(
        conversation["id"], workspace_id=DEMO_WORKSPACE_ID
    )["status"] == "blocked"

    # A worker that comes back after operator recovery must not publish its stale answer.
    assert store.complete_job_answer(
        job["id"],
        workspace_id=DEMO_WORKSPACE_ID,
        content="late stale answer",
        payload={"answer": "late stale answer"},
    ) is None
    assert [
        row for row in store.list_messages(conversation["id"], workspace_id=DEMO_WORKSPACE_ID)
        if row["role"] == "assistant"
    ] == []

    # Recovery does not silently replay. A human/operator retry creates a fresh job id.
    retried = store.retry_job(job["id"], workspace_id=DEMO_WORKSPACE_ID)
    assert retried["id"] != job["id"]
    assert retried["status"] == "queued"
    assert retried["attempts"] == 0
    assert retried["payload"] == recovered["payload"]


def test_recovery_does_not_clobber_job_completed_before_apply(tmp_path):
    store, conversation, job = _running_job(tmp_path)
    assert list_stale_running_jobs(
        store,
        stale_after_seconds=300,
        now=1000.0,
    )

    message = store.complete_job_answer(
        job["id"],
        workspace_id=DEMO_WORKSPACE_ID,
        content="authoritative answer",
        payload={"answer": "authoritative answer"},
    )
    assert message is not None

    changed = fail_stale_running_jobs(
        store,
        stale_after_seconds=300,
        now=1000.0,
    )
    assert changed == []
    completed = store.get_job(job["id"], workspace_id=DEMO_WORKSPACE_ID)
    assert completed["status"] == "completed"
    assert completed["last_error"] is None
    assert store.get_conversation(
        conversation["id"], workspace_id=DEMO_WORKSPACE_ID
    )["status"] == "review"


def test_recovery_scope_filter_does_not_cross_workspaces(tmp_path):
    store, _conversation, job = _running_job(tmp_path)

    assert list_stale_running_jobs(
        store,
        stale_after_seconds=300,
        now=1000.0,
        workspace_id="workspace-other",
    ) == []
    assert fail_stale_running_jobs(
        store,
        stale_after_seconds=300,
        now=1000.0,
        workspace_id="workspace-other",
    ) == []
    assert store.get_job(job["id"], workspace_id=DEMO_WORKSPACE_ID)["status"] == "running"
