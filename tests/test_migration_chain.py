from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


ROOT = Path(__file__).resolve().parents[1]


def test_alembic_upgrade_head_includes_memory_adapter_and_external_workflow_schema(tmp_path):
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
    assert "game_adapter_ticket_replays" in tables
    assert "external_issue_links" in tables
    assert "github_code_contexts" in tables

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

    replay_columns = {row["name"] for row in inspector.get_columns("game_adapter_ticket_replays")}
    assert {"ticket_id", "nonce", "expires_at", "consumed_at"} <= replay_columns
    replay_indexes = {row["name"] for row in inspector.get_indexes("game_adapter_ticket_replays")}
    assert "ix_game_adapter_ticket_replays_expires_at" in replay_indexes
    replay_unique_constraints = {
        row["name"] for row in inspector.get_unique_constraints("game_adapter_ticket_replays")
    }
    assert "uq_game_adapter_ticket_replays_nonce" in replay_unique_constraints

    external_link_columns = {
        row["name"] for row in inspector.get_columns("external_issue_links")
    }
    assert {
        "id",
        "workspace_id",
        "conversation_id",
        "provider",
        "resource_type",
        "repository",
        "external_key",
        "external_url",
        "external_title",
        "sync_state",
        "created_by",
        "meta",
        "created_at",
        "updated_at",
        "last_pushed_at",
    } <= external_link_columns
    external_link_indexes = {
        row["name"] for row in inspector.get_indexes("external_issue_links")
    }
    assert "ix_external_issue_links_workspace_conversation" in external_link_indexes
    assert "ix_external_issue_links_provider_target" in external_link_indexes
    external_link_unique_constraints = {
        row["name"] for row in inspector.get_unique_constraints("external_issue_links")
    }
    assert "uq_external_issue_link_target" in external_link_unique_constraints

    github_context_columns = {
        row["name"] for row in inspector.get_columns("github_code_contexts")
    }
    assert {
        "link_id",
        "workspace_id",
        "conversation_id",
        "repository",
        "pull_request_number",
        "head_sha",
        "head_ref",
        "base_sha",
        "base_ref",
        "selected_commit_sha",
        "selected_commit_url",
        "selected_commit_message",
        "updated_at",
    } <= github_context_columns
    github_context_indexes = {
        row["name"] for row in inspector.get_indexes("github_code_contexts")
    }
    assert "ix_github_code_contexts_workspace_conversation" in github_context_indexes
    assert "ix_github_code_contexts_repository_head_sha" in github_context_indexes
    assert "ix_github_code_contexts_repository_selected_sha" in github_context_indexes

    with engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert revision == "20260918_0009"
