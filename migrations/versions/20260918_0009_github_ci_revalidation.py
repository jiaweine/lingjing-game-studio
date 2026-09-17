"""Add indexed GitHub CI bindings and webhook delivery replay protection.

Revision ID: 20260918_0009
Revises: 20260918_0008
"""

import json
import re

from alembic import op
import sqlalchemy as sa

revision = "20260918_0009"
down_revision = "20260918_0008"
branch_labels = None
depends_on = None

_FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def upgrade() -> None:
    op.create_table(
        "github_code_bindings",
        sa.Column("link_id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", sa.String(length=64), nullable=False),
        sa.Column("repository", sa.String(length=240), nullable=False),
        sa.Column("pull_request_number", sa.Integer(), nullable=True),
        sa.Column("head_commit_sha", sa.String(length=40), nullable=True),
        sa.Column("selected_commit_sha", sa.String(length=40), nullable=True),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("link_id"),
    )
    op.create_index(
        "ix_github_code_bindings_repository_head",
        "github_code_bindings",
        ["repository", "head_commit_sha"],
    )
    op.create_index(
        "ix_github_code_bindings_repository_selected",
        "github_code_bindings",
        ["repository", "selected_commit_sha"],
    )
    op.create_index(
        "ix_github_code_bindings_workspace_conversation",
        "github_code_bindings",
        ["workspace_id", "conversation_id"],
    )

    op.create_table(
        "github_webhook_deliveries",
        sa.Column("delivery_id", sa.String(length=128), nullable=False),
        sa.Column("event", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=True),
        sa.Column("repository", sa.String(length=240), nullable=True),
        sa.Column("head_sha", sa.String(length=40), nullable=True),
        sa.Column("workflow_run_id", sa.String(length=64), nullable=True),
        sa.Column("workflow_name", sa.String(length=240), nullable=True),
        sa.Column("workflow_url", sa.Text(), nullable=True),
        sa.Column("conclusion", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("matched_count", sa.Integer(), nullable=False),
        sa.Column("enqueued_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("claimed_at", sa.Float(), nullable=False),
        sa.Column("completed_at", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("delivery_id"),
    )
    op.create_index(
        "ix_github_webhook_deliveries_status_created",
        "github_webhook_deliveries",
        ["status", "created_at"],
    )

    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            "SELECT id, workspace_id, conversation_id, repository, meta, updated_at "
            "FROM external_issue_links WHERE provider = 'github'"
        )
    ).mappings()
    for row in rows:
        try:
            meta = json.loads(row["meta"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        context = dict(meta.get("github_context") or {})
        if not context:
            continue
        pull = dict(context.get("pull_request") or {})
        head_sha = str(context.get("head_commit_sha") or "").strip().lower() or None
        selected_sha = str(context.get("selected_commit_sha") or "").strip().lower() or None
        if head_sha and not _FULL_SHA_RE.fullmatch(head_sha):
            head_sha = None
        if selected_sha and not _FULL_SHA_RE.fullmatch(selected_sha):
            selected_sha = None
        try:
            pull_number = int(pull.get("number")) if pull.get("number") is not None else None
        except (TypeError, ValueError):
            pull_number = None
        connection.execute(
            sa.text(
                "INSERT INTO github_code_bindings "
                "(link_id, workspace_id, conversation_id, repository, pull_request_number, "
                "head_commit_sha, selected_commit_sha, updated_at) "
                "VALUES (:link_id, :workspace_id, :conversation_id, :repository, :pull_number, "
                ":head_sha, :selected_sha, :updated_at)"
            ),
            {
                "link_id": row["id"],
                "workspace_id": row["workspace_id"],
                "conversation_id": row["conversation_id"],
                "repository": str(row["repository"] or "").lower(),
                "pull_number": pull_number,
                "head_sha": head_sha,
                "selected_sha": selected_sha,
                "updated_at": float(row["updated_at"] or 0.0),
            },
        )


def downgrade() -> None:
    op.drop_index(
        "ix_github_webhook_deliveries_status_created",
        table_name="github_webhook_deliveries",
    )
    op.drop_table("github_webhook_deliveries")
    op.drop_index(
        "ix_github_code_bindings_workspace_conversation",
        table_name="github_code_bindings",
    )
    op.drop_index(
        "ix_github_code_bindings_repository_selected",
        table_name="github_code_bindings",
    )
    op.drop_index(
        "ix_github_code_bindings_repository_head",
        table_name="github_code_bindings",
    )
    op.drop_table("github_code_bindings")
