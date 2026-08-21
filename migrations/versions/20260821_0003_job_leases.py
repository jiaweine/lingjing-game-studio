"""Add renewable leases and fencing tokens to analysis jobs.

Revision ID: 20260821_0003
Revises: 20260817_0002
"""

from alembic import op
import sqlalchemy as sa


revision = "20260821_0003"
down_revision = "20260817_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("analysis_jobs") as batch:
        batch.add_column(sa.Column("lease_token", sa.String(64), nullable=True))
        batch.add_column(sa.Column("heartbeat_at", sa.Float(), nullable=True))
        batch.add_column(sa.Column("lease_expires_at", sa.Float(), nullable=True))
        batch.create_index(
            "ix_analysis_jobs_lease_expires_at", ["lease_expires_at"]
        )

    # Jobs already marked running predate the lease protocol and have no owner
    # that can renew them. Return them to the durable queue exactly once.
    op.execute(
        "UPDATE analysis_jobs "
        "SET status = 'queued', worker_id = NULL, claimed_at = NULL, "
        "available_at = created_at, "
        "last_error = 'requeued during lease migration' "
        "WHERE status = 'running'"
    )


def downgrade() -> None:
    with op.batch_alter_table("analysis_jobs") as batch:
        batch.drop_index("ix_analysis_jobs_lease_expires_at")
        batch.drop_column("lease_expires_at")
        batch.drop_column("heartbeat_at")
        batch.drop_column("lease_token")
