from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


ROOT = Path(__file__).resolve().parents[1]


def test_alembic_upgrade_head_includes_memory_ingestion_schema_and_event_index(tmp_path):
    database = tmp_path / "migration-chain.db"
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database.as_posix()}")

    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{database.as_posix()}")
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert "context_memory_proposals" in tables
    assert "context_memory_ingestion_receipts" in tables

    receipt_columns = {row["name"] for row in inspector.get_columns("context_memory_ingestion_receipts")}
    assert {
        "event_id",
        "workspace_id",
        "conversation_id",
        "message_id",
        "project_id",
        "status",
        "attempts",
        "worker_id",
        "claimed_at",
        "available_at",
        "completed_at",
        "proposal_count",
        "last_error",
        "created_at",
        "updated_at",
    } <= receipt_columns

    receipt_indexes = {row["name"] for row in inspector.get_indexes("context_memory_ingestion_receipts")}
    assert "ix_context_memory_ingestion_receipts_status_available" in receipt_indexes
    assert "ix_context_memory_ingestion_receipts_workspace" in receipt_indexes
    assert "ix_context_memory_ingestion_receipts_conversation" in receipt_indexes
    assert "ix_context_memory_ingestion_receipts_message" in receipt_indexes
    assert "ix_context_memory_ingestion_receipts_project" in receipt_indexes

    task_event_indexes = {row["name"] for row in inspector.get_indexes("task_events")}
    assert "ix_task_events_type_id" in task_event_indexes

    with engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert revision == "20260907_0006"
