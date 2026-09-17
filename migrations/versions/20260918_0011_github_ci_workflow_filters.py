"""Add exact workflow-name filters for GitHub CI auto verification.

Revision ID: 20260918_0011
Revises: 20260918_0010
"""

from alembic import op
import sqlalchemy as sa

revision = "20260918_0011"
down_revision = "20260918_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "github_ci_workflow_filters",
        sa.Column("link_id", sa.String(length=64), nullable=False),
        sa.Column("workflow_name", sa.String(length=240), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("link_id"),
    )
    op.create_index(
        "ix_github_ci_workflow_filters_name",
        "github_ci_workflow_filters",
        ["workflow_name"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_github_ci_workflow_filters_name",
        table_name="github_ci_workflow_filters",
    )
    op.drop_table("github_ci_workflow_filters")
