from __future__ import annotations

import time

from sqlalchemy import update

from worldforge.context.memory_ingestion import MemoryIngestionConsumer
from worldforge.product.store import ConversationStore, DEMO_USER_ID, DEMO_WORKSPACE_ID


def test_stale_worker_cannot_overwrite_newer_receipt_attempt(tmp_path):
    product = ConversationStore(
        tmp_path / "product.db",
        tmp_path / "assets",
        seed_dev_identity=True,
    )
    conversation = product.create_conversation(
        "Lease fencing",
        "general",
        workspace_id=DEMO_WORKSPACE_ID,
        created_by=DEMO_USER_ID,
    )
    event = product.add_event(
        conversation["id"],
        "message.accepted",
        {"message_id": "unused-by-direct-claim-test"},
        workspace_id=DEMO_WORKSPACE_ID,
    )
    consumer = MemoryIngestionConsumer(
        product,
        auto_create_schema=True,
        lease_seconds=5.0,
    )

    first_claimed_at = time.time()
    first_attempt = consumer._claim(
        event,
        worker_id="worker-a",
        now=first_claimed_at,
    )
    assert first_attempt == 1

    # Deterministically expire A without sleeping. B must be able to steal the stale lease.
    with product.engine.begin() as connection:
        connection.execute(
            update(consumer.receipts)
            .where(consumer.receipts.c.event_id == event["id"])
            .values(claimed_at=first_claimed_at - consumer.lease_seconds - 1.0)
        )

    second_attempt = consumer._claim(
        event,
        worker_id="worker-b",
        now=time.time(),
    )
    assert second_attempt == 2

    assert consumer._finish(
        event["id"],
        worker_id="worker-b",
        status="completed",
        message_id="msg-new",
        project_id="project-new",
        proposal_count=1,
        attempts=second_attempt,
    ) is True

    # A returns late after B committed. Its old attempt token must be fenced out rather than
    # regressing the authoritative receipt to failed (or overwriting B's diagnostics).
    assert consumer._finish(
        event["id"],
        worker_id="worker-a",
        status="failed",
        message_id="msg-old",
        project_id="project-old",
        error="late stale failure",
        attempts=first_attempt,
    ) is False

    receipt = consumer.get_receipt(event["id"])
    assert receipt is not None
    assert receipt["status"] == "completed"
    assert receipt["attempts"] == 2
    assert receipt["worker_id"] == "worker-b"
    assert receipt["message_id"] == "msg-new"
    assert receipt["project_id"] == "project-new"
    assert receipt["proposal_count"] == 1
    assert receipt["last_error"] == ""
