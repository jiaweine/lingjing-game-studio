from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
import threading
import uuid

import pytest

from worldforge.product.store import ConversationStore


DATABASE_URL = os.getenv("WORLDFORGE_TEST_DATABASE_URL")


@pytest.mark.skipif(not DATABASE_URL, reason="requires WORLDFORGE_TEST_DATABASE_URL")
def test_postgres_claim_recovery_is_visible_across_store_instances(tmp_path):
    first_store = ConversationStore(
        asset_dir=tmp_path / "assets-a",
        database_url=DATABASE_URL,
        auto_create_schema=False,
        seed_dev_identity=False,
    )
    second_store = ConversationStore(
        asset_dir=tmp_path / "assets-b",
        database_url=DATABASE_URL,
        auto_create_schema=False,
        seed_dev_identity=False,
    )
    owner = first_store.create_user_workspace(
        email=f"lease-{uuid.uuid4().hex}@example.com",
        name="Lease Owner",
        password_hash="test-only",
        workspace_name=f"Lease Test {uuid.uuid4().hex[:8]}",
    )
    conversation = first_store.create_conversation(
        "PostgreSQL lease fencing",
        workspace_id=owner["workspace_id"],
        created_by=owner["user_id"],
    )
    job = first_store.enqueue_job(
        workspace_id=owner["workspace_id"],
        conversation_id=conversation["id"],
        payload={"text": "verify production claim semantics"},
    )

    first_claim = first_store.claim_job(
        "postgres-worker-a", job_id=job["id"], lease_seconds=10
    )
    assert first_claim is not None
    assert second_store.claim_job(
        "postgres-worker-b", job_id=job["id"], lease_seconds=10
    ) is None

    reclaim_at = first_claim["lease_expires_at"] + 1
    second_claim = second_store.claim_job(
        "postgres-worker-b",
        job_id=job["id"],
        lease_seconds=10,
        now=reclaim_at,
    )
    assert second_claim is not None
    assert second_claim["attempts"] == 2
    assert second_claim["lease_token"] != first_claim["lease_token"]

    assert first_store.complete_job_answer(
        job["id"],
        workspace_id=owner["workspace_id"],
        content="stale",
        payload={},
        lease_token=first_claim["lease_token"],
    ) is None
    assert second_store.complete_job_answer(
        job["id"],
        workspace_id=owner["workspace_id"],
        content="current",
        payload={},
        lease_token=second_claim["lease_token"],
    ) is not None

    race_job = first_store.enqueue_job(
        workspace_id=owner["workspace_id"],
        conversation_id=conversation["id"],
        payload={"text": "race two production workers"},
    )
    barrier = threading.Barrier(2)

    def race_claim(store, worker_id):
        barrier.wait(timeout=5)
        return store.claim_job(worker_id, job_id=race_job["id"])

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda args: race_claim(*args),
                (
                    (first_store, "postgres-worker-a"),
                    (second_store, "postgres-worker-b"),
                ),
            )
        )

    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    assert winners[0]["attempts"] == 1
