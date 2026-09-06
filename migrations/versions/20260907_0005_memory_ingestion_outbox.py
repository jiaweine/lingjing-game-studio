"""Durable memory-ingestion receipts for message.accepted outbox events.

Revision ID: 20260907_0005
Revises: 20260906_0004
"""

from alembic import op
import sqlalchemy as sa

revision = "20260907_0005"
down_revision = "20260906_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "context_memory_ingestion_receipts",
        sa.Column("event_id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.String(64), nullable=False),
        sa.Column("conversation_id", sa.String(64), nullable=False),
        sa.Column("message_id", sa.String(64), nullable=True),
        sa.Column("project_id", sa.String(64), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("worker_id", sa.String(96), nullable=True),
        sa.Column("claimed_at", sa.Float(), nullable=True),
        sa.Column("available_at", sa.Float(), nullable=False, server_default="0"),
        sa.Column("completed_at", sa.Float(), nullable=True),
        sa.Column("proposal_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
    )
    op.create_index(
        "ix_context_memory_ingestion_receipts_status_available",
        "context_memory_ingestion_receipts",
        ["status", "available_at"],
    )
    op.create_index(
        "ix_context_memory_ingestion_receipts_workspace",
        "context_memory_ingestion_receipts",
        ["workspace_id", "event_id"],
    )
    op.create_index(
        "ix_context_memory_ingestion_receipts_conversation",
        "context_memory_ingestion_receipts",
        ["conversation_id", "event_id"],
    )
    op.create_index(
        "ix_context_memory_ingestion_receipts_message",
        "context_memory_ingestion_receipts",
        ["message_id"],
    )
    op.create_index(
        "ix_context_memory_ingestion_receipts_project",
        "context_memory_ingestion_receipts",
        ["project_id", "event_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_context_memory_ingestion_receipts_project",
        table_name="context_memory_ingestion_receipts",
    )
    op.drop_index(
        "ix_context_memory_ingestion_receipts_message",
        table_name="context_memory_ingestion_receipts",
    )
    op.drop_index(
        "ix_context_memory_ingestion_receipts_conversation",
        table_name="context_memory_ingestion_receipts",
    )
    op.drop_index(
        "ix_context_memory_ingestion_receipts_workspace",
        table_name="context_memory_ingestion_receipts",
    )
    op.drop_index(
        "ix_context_memory_ingestion_receipts_status_available",
        table_name="context_memory_ingestion_receipts",
    )
    op.drop_table("context_memory_ingestion_receipts")
