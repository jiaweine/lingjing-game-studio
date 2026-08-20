from __future__ import annotations

from sqlalchemy.dialects import postgresql

from worldforge.product.store import ConversationStore
from worldforge.worker import _postgres_claim_statement, claim_external_job


def test_postgres_worker_claim_uses_skip_locked(tmp_path):
    store = ConversationStore(
        tmp_path / "product.db",
        tmp_path / "assets",
        seed_dev_identity=False,
    )
    statement = _postgres_claim_statement(store, 123.0)
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    ).upper()
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "STATUS = 'QUEUED'" in sql
    assert "AVAILABLE_AT <= 123.0" in sql
    assert "ORDER BY" in sql


def test_external_claim_falls_back_to_store_claim_on_sqlite(tmp_path):
    store = ConversationStore(
        tmp_path / "product.db",
        tmp_path / "assets",
        seed_dev_identity=False,
    )
    owner = store.create_user_workspace(
        email="queue@example.com",
        name="Queue Owner",
        password_hash="hashed",
        workspace_name="Queue Lab",
    )
    conversation = store.create_conversation(
        "claim",
        workspace_id=owner["workspace_id"],
        created_by=owner["user_id"],
    )
    queued = store.enqueue_job(
        workspace_id=owner["workspace_id"],
        conversation_id=conversation["id"],
        payload={"text": "claim", "asset_ids": []},
    )

    claimed = claim_external_job(store, "sqlite-worker")
    assert claimed is not None
    assert claimed["id"] == queued["id"]
    assert claimed["status"] == "running"
    assert claimed["worker_id"] == "sqlite-worker"
    assert claimed["attempts"] == 1
