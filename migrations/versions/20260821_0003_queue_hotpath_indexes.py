"""Index queue, replay and task-list hot paths.

Revision ID: 20260821_0003
Revises: 20260817_0002
"""

from alembic import op

revision = "20260821_0003"
down_revision = "20260817_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # External workers continuously query the oldest ready job. A status-only index
    # still leaves available_at/created_at filtering and ordering to a large scan.
    op.create_index(
        "ix_analysis_jobs_claim_ready",
        "analysis_jobs",
        ["status", "available_at", "created_at"],
    )
    # Lease recovery scans only running jobs whose heartbeat timestamp is stale.
    op.create_index(
        "ix_analysis_jobs_lease_reap",
        "analysis_jobs",
        ["status", "claimed_at"],
    )
    # Conversation views repeatedly ask for the latest job.
    op.create_index(
        "ix_analysis_jobs_conversation_latest",
        "analysis_jobs",
        ["workspace_id", "conversation_id", "created_at"],
    )
    # Durable WebSocket replay is scoped to one workspace/conversation and cursor.
    op.create_index(
        "ix_task_events_conversation_cursor",
        "task_events",
        ["workspace_id", "conversation_id", "id"],
    )
    # Message history and the human-feedback gate both read a conversation in time order.
    op.create_index(
        "ix_messages_conversation_role_created",
        "messages",
        ["conversation_id", "role", "created_at", "id"],
    )
    # Sidebar task listing filters by workspace/archive state then sorts pinned/recent.
    op.create_index(
        "ix_conversations_workspace_archive_order",
        "conversations",
        ["workspace_id", "archived_at", "pinned", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_conversations_workspace_archive_order",
        table_name="conversations",
    )
    op.drop_index(
        "ix_messages_conversation_role_created",
        table_name="messages",
    )
    op.drop_index(
        "ix_task_events_conversation_cursor",
        table_name="task_events",
    )
    op.drop_index(
        "ix_analysis_jobs_conversation_latest",
        table_name="analysis_jobs",
    )
    op.drop_index(
        "ix_analysis_jobs_lease_reap",
        table_name="analysis_jobs",
    )
    op.drop_index(
        "ix_analysis_jobs_claim_ready",
        table_name="analysis_jobs",
    )
