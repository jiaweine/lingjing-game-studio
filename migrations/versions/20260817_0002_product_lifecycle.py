"""Product lifecycle, collaboration, approval and feedback schema.

Revision ID: 20260817_0002
Revises: 20260815_0001
"""

from alembic import op
import sqlalchemy as sa

revision = "20260817_0002"
down_revision = "20260815_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("conversations") as batch:
        batch.add_column(sa.Column("assigned_to", sa.String(64), nullable=True))
        batch.add_column(sa.Column("status", sa.String(32), nullable=False, server_default="active"))
        batch.add_column(sa.Column("pinned", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("archived_at", sa.Float(), nullable=True))
        batch.create_index("ix_conversations_assigned_to", ["assigned_to"])
        batch.create_index("ix_conversations_status", ["status"])
        batch.create_index("ix_conversations_archived_at", ["archived_at"])

    op.execute("UPDATE conversations SET assigned_to = created_by WHERE assigned_to IS NULL")

    op.create_table(
        "workspace_invites",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("workspace_id", sa.String(64), nullable=False),
        sa.Column("token", sa.String(96), nullable=False, unique=True),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False),
        sa.Column("accepted_by", sa.String(64), nullable=True),
        sa.Column("accepted_at", sa.Float(), nullable=True),
    )
    op.create_index("ix_workspace_invites_workspace_id", "workspace_invites", ["workspace_id"])
    op.create_index("ix_workspace_invites_token", "workspace_invites", ["token"], unique=True)
    op.create_index("ix_workspace_invites_status", "workspace_invites", ["status"])

    op.create_table(
        "approval_requests",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("workspace_id", sa.String(64), nullable=False),
        sa.Column("conversation_id", sa.String(64), nullable=False),
        sa.Column("action", sa.String(96), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("payload", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("requested_by", sa.String(64), nullable=False),
        sa.Column("resolved_by", sa.String(64), nullable=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("resolved_at", sa.Float(), nullable=True),
    )
    op.create_index("ix_approval_requests_workspace_id", "approval_requests", ["workspace_id"])
    op.create_index("ix_approval_requests_conversation_id", "approval_requests", ["conversation_id"])
    op.create_index("ix_approval_requests_status", "approval_requests", ["status"])

    op.create_table(
        "result_feedback",
        sa.Column("message_id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), primary_key=True),
        sa.Column("workspace_id", sa.String(64), nullable=False),
        sa.Column("conversation_id", sa.String(64), nullable=False),
        sa.Column("verdict", sa.String(32), nullable=False),
        sa.Column("evidence_useful", sa.Integer(), nullable=True),
        sa.Column("human_verified", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
    )
    op.create_index("ix_result_feedback_workspace_id", "result_feedback", ["workspace_id"])
    op.create_index("ix_result_feedback_conversation_id", "result_feedback", ["conversation_id"])

    op.create_table(
        "product_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("conversation_id", sa.String(64), nullable=True),
        sa.Column("name", sa.String(96), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.Float(), nullable=False),
    )
    op.create_index("ix_product_events_workspace_id", "product_events", ["workspace_id"])
    op.create_index("ix_product_events_conversation_id", "product_events", ["conversation_id"])
    op.create_index("ix_product_events_name", "product_events", ["name"])


def downgrade() -> None:
    op.drop_index("ix_product_events_name", table_name="product_events")
    op.drop_index("ix_product_events_conversation_id", table_name="product_events")
    op.drop_index("ix_product_events_workspace_id", table_name="product_events")
    op.drop_table("product_events")

    op.drop_index("ix_result_feedback_conversation_id", table_name="result_feedback")
    op.drop_index("ix_result_feedback_workspace_id", table_name="result_feedback")
    op.drop_table("result_feedback")

    op.drop_index("ix_approval_requests_status", table_name="approval_requests")
    op.drop_index("ix_approval_requests_conversation_id", table_name="approval_requests")
    op.drop_index("ix_approval_requests_workspace_id", table_name="approval_requests")
    op.drop_table("approval_requests")

    op.drop_index("ix_workspace_invites_status", table_name="workspace_invites")
    op.drop_index("ix_workspace_invites_token", table_name="workspace_invites")
    op.drop_index("ix_workspace_invites_workspace_id", table_name="workspace_invites")
    op.drop_table("workspace_invites")

    with op.batch_alter_table("conversations") as batch:
        batch.drop_index("ix_conversations_archived_at")
        batch.drop_index("ix_conversations_status")
        batch.drop_index("ix_conversations_assigned_to")
        batch.drop_column("archived_at")
        batch.drop_column("pinned")
        batch.drop_column("status")
        batch.drop_column("assigned_to")
