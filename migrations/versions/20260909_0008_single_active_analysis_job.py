"""Enforce one queued/running analysis job per conversation.

Revision ID: 20260909_0008
Revises: 20260909_0007
"""

from alembic import op
import sqlalchemy as sa

revision = "20260909_0008"
down_revision = "20260909_0007"
branch_labels = None
depends_on = None

_INDEX_NAME = "uq_analysis_jobs_active_conversation"
_ACTIVE = "status IN ('queued', 'running')"


def upgrade() -> None:
    connection = op.get_bind()
    duplicate = connection.execute(
        sa.text(
            "SELECT workspace_id, conversation_id, COUNT(*) AS active_count "
            "FROM analysis_jobs "
            "WHERE status IN ('queued', 'running') "
            "GROUP BY workspace_id, conversation_id "
            "HAVING COUNT(*) > 1 LIMIT 1"
        )
    ).first()
    if duplicate is not None:
        raise RuntimeError(
            "cannot enforce single active analysis job: existing duplicate queued/running "
            f"jobs for workspace={duplicate.workspace_id!r} "
            f"conversation={duplicate.conversation_id!r}; resolve them before migration"
        )

    op.create_index(
        _INDEX_NAME,
        "analysis_jobs",
        ["workspace_id", "conversation_id"],
        unique=True,
        sqlite_where=sa.text(_ACTIVE),
        postgresql_where=sa.text(_ACTIVE),
    )


def downgrade() -> None:
    op.drop_index(_INDEX_NAME, table_name="analysis_jobs")
