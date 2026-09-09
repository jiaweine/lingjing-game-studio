from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text


ROOT = Path(__file__).resolve().parents[1]


def _config(database) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database.as_posix()}")
    return config


def test_pending_approval_migration_fails_closed_on_existing_duplicates(tmp_path):
    database = tmp_path / "migration-guard.db"
    config = _config(database)
    command.upgrade(config, "20260909_0008")

    engine = create_engine(f"sqlite:///{database.as_posix()}")
    now = 1.0
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO approval_requests "
                "(id, workspace_id, conversation_id, action, status, reason, payload, "
                "requested_by, resolved_by, created_at, resolved_at) "
                "VALUES "
                "('approval-a', 'ws', 'cv', 'conversation.delete', 'pending', '', '{}', "
                "'user-a', NULL, :now, NULL), "
                "('approval-b', 'ws', 'cv', 'conversation.delete', 'pending', '', '{}', "
                "'user-b', NULL, :now, NULL)"
            ),
            {"now": now},
        )

    with pytest.raises(RuntimeError, match="existing duplicate pending requests"):
        command.upgrade(config, "20260909_0009")

    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT id, status FROM approval_requests "
                "WHERE workspace_id='ws' AND conversation_id='cv' "
                "AND action='conversation.delete' ORDER BY id"
            )
        ).fetchall()
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()

    assert rows == [("approval-a", "pending"), ("approval-b", "pending")]
    assert revision == "20260909_0008"
