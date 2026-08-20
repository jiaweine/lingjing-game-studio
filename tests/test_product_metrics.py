from __future__ import annotations

import uuid

from worldforge.product.metrics import calculate_product_metrics
from worldforge.product.store import ConversationStore


def _owner(store: ConversationStore):
    return store.create_user_workspace(
        email=f"metrics-{uuid.uuid4().hex[:10]}@example.com",
        name="Metrics Owner",
        password_hash="hashed",
        workspace_name="Metrics Lab",
    )


def test_metrics_distinguish_first_attempt_eventual_completion_and_recovery(tmp_path):
    store = ConversationStore(
        tmp_path / "product.db",
        tmp_path / "assets",
        seed_dev_identity=False,
    )
    owner = _owner(store)
    workspace_id = owner["workspace_id"]
    conversation = store.create_conversation(
        "retry metrics",
        workspace_id=workspace_id,
        created_by=owner["user_id"],
    )

    first = store.enqueue_job(
        workspace_id=workspace_id,
        conversation_id=conversation["id"],
        payload={"text": "first", "asset_ids": []},
    )
    assert store.claim_job("metrics-worker", job_id=first["id"])
    store.cancel_job(first["id"], workspace_id=workspace_id)

    retry = store.retry_job(first["id"], workspace_id=workspace_id)
    assert store.claim_job("metrics-worker", job_id=retry["id"])
    assert store.complete_job_answer(
        retry["id"],
        workspace_id=workspace_id,
        content="done",
        payload={"evidence": [], "deliverables": []},
    )

    metrics = calculate_product_metrics(store, workspace_id=workspace_id)
    assert metrics["first_attempt_completion_rate"] == 0.0
    assert metrics["eventual_task_completion_rate"] == 1.0
    assert metrics["first_task_completion_rate"] == 1.0  # compatibility alias
    assert metrics["recovery_rate"] == 1.0
    assert metrics["interruption_rate"] == 0.5
    assert metrics["failure_rate"] == 0.0
    assert metrics["avg_time_to_first_result_seconds"] is not None


def test_metrics_empty_workspace_is_stable_and_does_not_divide_by_zero(tmp_path):
    store = ConversationStore(
        tmp_path / "product.db",
        tmp_path / "assets",
        seed_dev_identity=False,
    )
    owner = _owner(store)
    metrics = calculate_product_metrics(store, workspace_id=owner["workspace_id"])

    assert metrics["task_count"] == 0
    assert metrics["active_tasks"] == 0
    assert metrics["first_attempt_completion_rate"] == 0.0
    assert metrics["eventual_task_completion_rate"] == 0.0
    assert metrics["recovery_rate"] == 0.0
    assert metrics["avg_time_to_first_result_seconds"] is None
