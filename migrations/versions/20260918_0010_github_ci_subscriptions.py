"""Add explicit GitHub CI subscriptions and webhook delivery dedupe.

Revision ID: 20260918_0010
Revises: 20260918_0009
"""

from alembic import op
import sqlalchemy as sa

revision = "20260918_0010"
down_revision = "20260918_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "github_ci_subscriptions",
        sa.Column("link_id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.String(length=64), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("link_id"),
    )
    op.create_index(
        "ix_github_ci_subscriptions_workspace_conversation",
        "github_ci_subscriptions",
        ["workspace_id", "conversation_id"],
    )
    op.create_table(
        "github_ci_deliveries",
        sa.Column("delivery_id", sa.String(length=128), nullable=False),
        sa.Column("repository", sa.String(length=240), nullable=False),
        sa.Column("head_sha", sa.String(length=40), nullable=False),
        sa.Column("workflow_run_id", sa.BigInteger(), nullable=False),
        sa.Column("workflow_name", sa.String(length=240), nullable=False),
        sa.Column("conclusion", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("matched_routes", sa.Integer(), nullable=False),
        sa.Column("enqueued_jobs", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("received_at", sa.Float(), nullable=False),
        sa.Column("processed_at", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("delivery_id"),
    )
    op.create_index(
        "ix_github_ci_deliveries_repository_head_sha",
        "github_ci_deliveries",
        ["repository", "head_sha"],
    )
    op.create_index(
        "ix_github_ci_deliveries_workflow_run",
        "github_ci_deliveries",
        ["workflow_run_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_github_ci_deliveries_workflow_run",
        table_name="github_ci_deliveries",
    )
    op.drop_index(
        "ix_github_ci_deliveries_repository_head_sha",
        table_name="github_ci_deliveries",
    )
    op.drop_table("github_ci_deliveries")
    op.drop_index(
        "ix_github_ci_subscriptions_workspace_conversation",
        table_name="github_ci_subscriptions",
    )
    op.drop_table("github_ci_subscriptions")
