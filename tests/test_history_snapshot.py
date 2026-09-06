from __future__ import annotations

from worldforge.context.history_snapshot import (
    build_history_snapshot,
    history_from_job_payload,
    materialize_history_snapshot,
)
from worldforge.product.store import ConversationStore, DEMO_USER_ID, DEMO_WORKSPACE_ID


def _setup(tmp_path):
    store = ConversationStore(
        tmp_path / "product.db",
        tmp_path / "assets",
        seed_dev_identity=True,
    )
    conversation = store.create_conversation(
        "Long task",
        "general",
        workspace_id=DEMO_WORKSPACE_ID,
        created_by=DEMO_USER_ID,
    )
    return store, conversation


def test_history_boundary_is_constant_size_and_materializes_exact_prefix(tmp_path):
    store, conversation = _setup(tmp_path)
    for index in range(120):
        store.add_message(
            conversation["id"],
            "user" if index % 2 == 0 else "assistant",
            f"message-{index}-" + ("x" * 500),
            workspace_id=DEMO_WORKSPACE_ID,
        )
    history = store.list_messages(conversation["id"], workspace_id=DEMO_WORKSPACE_ID)
    snapshot = build_history_snapshot(history)
    assert set(snapshot) == {"mode", "count", "last_message_id", "digest"}
    assert snapshot["count"] == 120
    assert len(repr(snapshot)) < 300

    # A later message must not enter the old job's context.
    store.add_message(
        conversation["id"],
        "user",
        "future message",
        workspace_id=DEMO_WORKSPACE_ID,
    )
    restored, stats = materialize_history_snapshot(
        store,
        conversation_id=conversation["id"],
        workspace_id=DEMO_WORKSPACE_ID,
        snapshot=snapshot,
    )
    assert stats["history_snapshot_valid"] is True
    assert len(restored) == 120
    assert restored[-1]["id"] == snapshot["last_message_id"]
    assert all(row["content"] != "future message" for row in restored)


def test_history_digest_mismatch_never_substitutes_current_history(tmp_path):
    store, conversation = _setup(tmp_path)
    store.add_message(
        conversation["id"], "user", "alpha", workspace_id=DEMO_WORKSPACE_ID
    )
    history = store.list_messages(conversation["id"], workspace_id=DEMO_WORKSPACE_ID)
    snapshot = build_history_snapshot(history)
    snapshot["digest"] = "0" * 64

    restored, stats = materialize_history_snapshot(
        store,
        conversation_id=conversation["id"],
        workspace_id=DEMO_WORKSPACE_ID,
        snapshot=snapshot,
    )
    assert restored == []
    assert stats["history_snapshot_invalidated"] is True
    assert stats["history_snapshot_reason"] == "boundary-or-digest-mismatch"


def test_old_jobs_with_legacy_history_remain_readable(tmp_path):
    store, conversation = _setup(tmp_path)
    legacy = [{"id": "m1", "role": "user", "content": "old"}]
    restored, stats = history_from_job_payload(
        store,
        conversation_id=conversation["id"],
        workspace_id=DEMO_WORKSPACE_ID,
        payload={"history": legacy},
    )
    assert restored == legacy
    assert stats["history_snapshot_legacy_payload"] is True
