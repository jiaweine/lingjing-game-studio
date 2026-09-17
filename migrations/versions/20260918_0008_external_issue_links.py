"""Persist external issue linkage for Bug -> Fix workflows.

Revision ID: 20260918_0008
Revises: 20260909_0007
"""

from alembic import op
import sqlalchemy as sa

revision = "20260918_0008"
down_revision = "20260909_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "external_issue_links",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("resource_type", sa.String(length=32), nullable=False),
        sa.Column("repository", sa.String(length=240), nullable=False),
        sa.Column("external_key", sa.String(length=96), nullable=False),
        sa.Column("external_url", sa.Text(), nullable=False),
        sa.Column("external_title", sa.String(length=240), nullable=True),
        sa.Column("sync_state", sa.String(length=32), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("meta", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.Column("last_pushed_at", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "conversation_id",
            "provider",
            "repository",
            "external_key",
            name="uq_external_issue_link_target",
        ),
    )
    op.create_index(
        "ix_external_issue_links_workspace_conversation",
        "external_issue_links",
        ["workspace_id", "conversation_id"],
    )
    op.create_index(
        "ix_external_issue_links_provider_target",
        "external_issue_links",
        ["provider", "repository", "external_key"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_external_issue_links_provider_target",
        table_name="external_issue_links",
    )
    op.drop_index(
        "ix_external_issue_links_workspace_conversation",
        table_name="external_issue_links",
    )
    op.drop_table("external_issue_links")
