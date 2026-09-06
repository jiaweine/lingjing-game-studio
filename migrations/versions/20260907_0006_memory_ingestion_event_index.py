"""Index durable message-ingestion outbox events for bounded recovery scans.

Revision ID: 20260907_0006
Revises: 20260907_0005
"""

from alembic import op

revision = "20260907_0006"
down_revision = "20260907_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # MemoryIngestionConsumer repeatedly filters immutable task_events by type and advances
    # in event-id order. Without this index, low-frequency idle recovery can devolve into
    # repeated scans across progress/evidence events as a workspace grows.
    op.create_index(
        "ix_task_events_type_id",
        "task_events",
        ["type", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_task_events_type_id", table_name="task_events")
