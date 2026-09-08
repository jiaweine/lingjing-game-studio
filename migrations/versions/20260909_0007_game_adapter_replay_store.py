"""Persist Frozen Kernel GameAdapter ticket consumption across workers.

Revision ID: 20260909_0007
Revises: 20260907_0006
"""

from alembic import op
import sqlalchemy as sa

revision = "20260909_0007"
down_revision = "20260907_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "game_adapter_ticket_replays",
        sa.Column("ticket_id", sa.String(length=64), nullable=False),
        sa.Column("nonce", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False),
        sa.Column("consumed_at", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("ticket_id"),
        sa.UniqueConstraint("nonce", name="uq_game_adapter_ticket_replays_nonce"),
    )
    op.create_index(
        "ix_game_adapter_ticket_replays_expires_at",
        "game_adapter_ticket_replays",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_game_adapter_ticket_replays_expires_at",
        table_name="game_adapter_ticket_replays",
    )
    op.drop_table("game_adapter_ticket_replays")
