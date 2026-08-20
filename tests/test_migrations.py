from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


ROOT = Path(__file__).resolve().parents[1]


def test_alembic_upgrade_head_creates_queue_hotpath_indexes(tmp_path, monkeypatch):
    database = tmp_path / "migrations.db"
    monkeypatch.delenv("DATABASE_URL", raising=False)

    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database.as_posix()}")
    command.upgrade(config, "head")

    inspector = inspect(create_engine(f"sqlite:///{database.as_posix()}"))
    job_indexes = {row["name"] for row in inspector.get_indexes("analysis_jobs")}
    assert {
        "ix_analysis_jobs_claim_ready",
        "ix_analysis_jobs_lease_reap",
        "ix_analysis_jobs_conversation_latest",
    } <= job_indexes

    event_indexes = {row["name"] for row in inspector.get_indexes("task_events")}
    assert "ix_task_events_conversation_cursor" in event_indexes
    message_indexes = {row["name"] for row in inspector.get_indexes("messages")}
    assert "ix_messages_conversation_role_created" in message_indexes
    conversation_indexes = {
        row["name"] for row in inspector.get_indexes("conversations")
    }
    assert "ix_conversations_workspace_archive_order" in conversation_indexes

    # Downgrade is part of the migration contract too; exercise it instead of only
    # verifying that the happy-path upgrade compiles.
    command.downgrade(config, "base")
    remaining = inspect(create_engine(f"sqlite:///{database.as_posix()}"))
    assert "analysis_jobs" not in remaining.get_table_names()
