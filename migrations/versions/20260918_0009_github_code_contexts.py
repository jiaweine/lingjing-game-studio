"""Index GitHub PR/commit context for CI routing.

Revision ID: 20260918_0009
Revises: 20260918_0008
"""

from alembic import op
import sqlalchemy as sa

revision = "20260918_0009"
down_revision = "20260918_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "github_code_contexts",
        sa.Column("link_id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", sa.String(length=64), nullable=False),
        sa.Column("repository", sa.String(length=240), nullable=False),
        sa.Column("pull_request_number", sa.Integer(), nullable=True),
        sa.Column("head_sha", sa.String(length=40), nullable=True),
        sa.Column("head_ref", sa.String(length=240), nullable=True),
        sa.Column("base_sha", sa.String(length=40), nullable=True),
        sa.Column("base_ref", sa.String(length=240), nullable=True),
        sa.Column("selected_commit_sha", sa.String(length=40), nullable=True),
        sa.Column("selected_commit_url", sa.String(length=2000), nullable=True),
        sa.Column("selected_commit_message", sa.String(length=240), nullable=True),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("link_id"),
        sa.UniqueConstraint("link_id", name="uq_github_code_context_link"),
    )
    op.create_index(
        "ix_github_code_contexts_workspace_conversation",
        "github_code_contexts",
        ["workspace_id", "conversation_id"],
    )
    op.create_index(
        "ix_github_code_contexts_repository_head_sha",
        "github_code_contexts",
        ["repository", "head_sha"],
    )
    op.create_index(
        "ix_github_code_contexts_repository_selected_sha",
        "github_code_contexts",
        ["repository", "selected_commit_sha"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_github_code_contexts_repository_selected_sha",
        table_name="github_code_contexts",
    )
    op.drop_index(
        "ix_github_code_contexts_repository_head_sha",
        table_name="github_code_contexts",
    )
    op.drop_index(
        "ix_github_code_contexts_workspace_conversation",
        table_name="github_code_contexts",
    )
    op.drop_table("github_code_contexts")
