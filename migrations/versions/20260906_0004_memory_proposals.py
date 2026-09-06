"""Governed project-memory consolidation proposals.

Revision ID: 20260906_0004
Revises: 20260905_0003
"""

from alembic import op
import sqlalchemy as sa

revision = "20260906_0004"
down_revision = "20260905_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "context_memory_proposals",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("workspace_id", sa.String(64), nullable=False),
        sa.Column("project_id", sa.String(64), nullable=False),
        sa.Column("conversation_id", sa.String(64), nullable=False),
        sa.Column("message_id", sa.String(64), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("suggested_key", sa.String(240), nullable=False),
        sa.Column("kind", sa.String(48), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("build_ref", sa.String(160), nullable=True),
        sa.Column("branch_ref", sa.String(200), nullable=True),
        sa.Column("commit_ref", sa.String(160), nullable=True),
        sa.Column("environment_ref", sa.String(160), nullable=True),
        sa.Column("extractor_version", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("reviewed_by", sa.String(64), nullable=True),
        sa.Column("reviewed_at", sa.Float(), nullable=True),
        sa.Column("approved_memory_id", sa.String(64), nullable=True),
        sa.Column("review_note", sa.Text(), nullable=False, server_default=""),
        sa.UniqueConstraint(
            "project_id",
            "message_id",
            "fingerprint",
            name="uq_context_memory_proposal_source",
        ),
    )
    op.create_index(
        "ix_context_memory_proposals_project_status",
        "context_memory_proposals",
        ["project_id", "status", "created_at"],
    )
    op.create_index(
        "ix_context_memory_proposals_conversation",
        "context_memory_proposals",
        ["conversation_id", "created_at"],
    )
    op.create_index(
        "ix_context_memory_proposals_message",
        "context_memory_proposals",
        ["message_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_context_memory_proposals_message",
        table_name="context_memory_proposals",
    )
    op.drop_index(
        "ix_context_memory_proposals_conversation",
        table_name="context_memory_proposals",
    )
    op.drop_index(
        "ix_context_memory_proposals_project_status",
        table_name="context_memory_proposals",
    )
    op.drop_table("context_memory_proposals")
