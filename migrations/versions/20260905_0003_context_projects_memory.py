"""Explicit project scope and versioned ContextOS project memory.

Revision ID: 20260905_0003
Revises: 20260817_0002
"""

from alembic import op
import sqlalchemy as sa

revision = "20260905_0003"
down_revision = "20260817_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "context_projects",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("workspace_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(96), nullable=False),
        sa.Column("external_ref", sa.Text(), nullable=True),
        sa.Column("default_branch", sa.String(160), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.UniqueConstraint("workspace_id", "slug", name="uq_context_projects_workspace_slug"),
    )
    op.create_index("ix_context_projects_workspace_id", "context_projects", ["workspace_id"])
    op.create_index("ix_context_projects_status", "context_projects", ["status"])

    op.create_table(
        "context_project_conversations",
        sa.Column("workspace_id", sa.String(64), nullable=False),
        sa.Column("project_id", sa.String(64), nullable=False),
        sa.Column("conversation_id", sa.String(64), nullable=False),
        sa.Column("bound_by", sa.String(64), nullable=False),
        sa.Column("bound_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("project_id", "conversation_id"),
        sa.UniqueConstraint(
            "workspace_id",
            "conversation_id",
            name="uq_context_project_conversation_workspace",
        ),
    )
    op.create_index(
        "ix_context_project_conversations_workspace",
        "context_project_conversations",
        ["workspace_id"],
    )
    op.create_index(
        "ix_context_project_conversations_conversation",
        "context_project_conversations",
        ["conversation_id"],
    )

    # Every row is an immutable semantic-memory version. The current version is selected
    # through context_memory_heads, avoiding in-place fact mutation and making provenance
    # / rollback / audit deterministic under concurrent workers.
    op.create_table(
        "context_memory_items",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("workspace_id", sa.String(64), nullable=False),
        sa.Column("project_id", sa.String(64), nullable=False),
        sa.Column("memory_key", sa.String(240), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(48), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("value_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1"),
        sa.Column("importance", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("pinned", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("build_ref", sa.String(160), nullable=True),
        sa.Column("branch_ref", sa.String(200), nullable=True),
        sa.Column("commit_ref", sa.String(160), nullable=True),
        sa.Column("environment_ref", sa.String(160), nullable=True),
        sa.Column("valid_from", sa.Float(), nullable=True),
        sa.Column("valid_to", sa.Float(), nullable=True),
        sa.Column("expires_at", sa.Float(), nullable=True),
        sa.Column("source_type", sa.String(48), nullable=False),
        sa.Column("source_id", sa.String(128), nullable=False),
        sa.Column("source_excerpt", sa.Text(), nullable=False, server_default=""),
        sa.Column("supersedes_id", sa.String(64), nullable=True),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.UniqueConstraint(
            "project_id",
            "memory_key",
            "revision",
            name="uq_context_memory_revision",
        ),
    )
    op.create_index(
        "ix_context_memory_items_project_key",
        "context_memory_items",
        ["project_id", "memory_key"],
    )
    op.create_index(
        "ix_context_memory_items_workspace",
        "context_memory_items",
        ["workspace_id"],
    )
    op.create_index(
        "ix_context_memory_items_scope",
        "context_memory_items",
        ["project_id", "build_ref", "branch_ref", "commit_ref"],
    )
    op.create_index(
        "ix_context_memory_items_expires_at",
        "context_memory_items",
        ["expires_at"],
    )

    op.create_table(
        "context_memory_heads",
        sa.Column("workspace_id", sa.String(64), nullable=False),
        sa.Column("project_id", sa.String(64), nullable=False),
        sa.Column("memory_key", sa.String(240), nullable=False),
        sa.Column("memory_id", sa.String(64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("project_id", "memory_key"),
    )
    op.create_index(
        "ix_context_memory_heads_workspace",
        "context_memory_heads",
        ["workspace_id"],
    )
    op.create_index(
        "ix_context_memory_heads_project_state",
        "context_memory_heads",
        ["project_id", "state"],
    )

    op.create_table(
        "context_memory_relations",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("workspace_id", sa.String(64), nullable=False),
        sa.Column("project_id", sa.String(64), nullable=False),
        sa.Column("from_key", sa.String(240), nullable=False),
        sa.Column("relation", sa.String(64), nullable=False),
        sa.Column("to_key", sa.String(240), nullable=False),
        sa.Column("source_id", sa.String(128), nullable=True),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.UniqueConstraint(
            "project_id",
            "from_key",
            "relation",
            "to_key",
            name="uq_context_memory_relation",
        ),
    )
    op.create_index(
        "ix_context_memory_relations_project_from",
        "context_memory_relations",
        ["project_id", "from_key"],
    )
    op.create_index(
        "ix_context_memory_relations_project_to",
        "context_memory_relations",
        ["project_id", "to_key"],
    )

    op.create_table(
        "context_memory_usage",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.String(64), nullable=False),
        sa.Column("project_id", sa.String(64), nullable=False),
        sa.Column("memory_id", sa.String(64), nullable=False),
        sa.Column("memory_key", sa.String(240), nullable=False),
        sa.Column("conversation_id", sa.String(64), nullable=True),
        sa.Column("reason", sa.String(96), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("used_at", sa.Float(), nullable=False),
    )
    op.create_index(
        "ix_context_memory_usage_project",
        "context_memory_usage",
        ["project_id", "used_at"],
    )
    op.create_index(
        "ix_context_memory_usage_memory",
        "context_memory_usage",
        ["memory_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_context_memory_usage_memory", table_name="context_memory_usage")
    op.drop_index("ix_context_memory_usage_project", table_name="context_memory_usage")
    op.drop_table("context_memory_usage")

    op.drop_index("ix_context_memory_relations_project_to", table_name="context_memory_relations")
    op.drop_index("ix_context_memory_relations_project_from", table_name="context_memory_relations")
    op.drop_table("context_memory_relations")

    op.drop_index("ix_context_memory_heads_project_state", table_name="context_memory_heads")
    op.drop_index("ix_context_memory_heads_workspace", table_name="context_memory_heads")
    op.drop_table("context_memory_heads")

    op.drop_index("ix_context_memory_items_expires_at", table_name="context_memory_items")
    op.drop_index("ix_context_memory_items_scope", table_name="context_memory_items")
    op.drop_index("ix_context_memory_items_workspace", table_name="context_memory_items")
    op.drop_index("ix_context_memory_items_project_key", table_name="context_memory_items")
    op.drop_table("context_memory_items")

    op.drop_index(
        "ix_context_project_conversations_conversation",
        table_name="context_project_conversations",
    )
    op.drop_index(
        "ix_context_project_conversations_workspace",
        table_name="context_project_conversations",
    )
    op.drop_table("context_project_conversations")

    op.drop_index("ix_context_projects_status", table_name="context_projects")
    op.drop_index("ix_context_projects_workspace_id", table_name="context_projects")
    op.drop_table("context_projects")
