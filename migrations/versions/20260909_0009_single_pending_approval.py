"""Enforce one pending approval per conversation action.

Revision ID: 20260909_0009
Revises: 20260909_0008
"""

from alembic import op
import sqlalchemy as sa

revision = "20260909_0009"
down_revision = "20260909_0008"
branch_labels = None
depends_on = None

_INDEX_NAME = "uq_approval_requests_pending_action"
_PENDING = "status = 'pending'"


def upgrade() -> None:
    connection = op.get_bind()
    duplicate = connection.execute(
        sa.text(
            "SELECT workspace_id, conversation_id, action, COUNT(*) AS pending_count "
            "FROM approval_requests "
            "WHERE status = 'pending' "
            "GROUP BY workspace_id, conversation_id, action "
            "HAVING COUNT(*) > 1 LIMIT 1"
        )
    ).first()
    if duplicate is not None:
        raise RuntimeError(
            "cannot enforce single pending approval: existing duplicate pending requests "
            f"for workspace={duplicate.workspace_id!r} "
            f"conversation={duplicate.conversation_id!r} action={duplicate.action!r}; "
            "resolve them before migration"
        )

    op.create_index(
        _INDEX_NAME,
        "approval_requests",
        ["workspace_id", "conversation_id", "action"],
        unique=True,
        sqlite_where=sa.text(_PENDING),
        postgresql_where=sa.text(_PENDING),
    )


def downgrade() -> None:
    op.drop_index(_INDEX_NAME, table_name="approval_requests")
