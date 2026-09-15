"""Phase 8: durable sync job orchestration.

Revision ID: f0a1b2c3d4e5
Revises: e9f0a1b2c3d4
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f0a1b2c3d4e5"
down_revision: str | None = "e9f0a1b2c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sync_jobs",
        sa.Column("sync_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("mapping_version_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("trigger", sa.String(32), nullable=False),
        sa.Column("coalesced_triggers", sa.JSON(), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=True),
        sa.Column("idempotency_key", sa.String(120), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("correlation_id", sa.String(64), nullable=False),
        sa.Column("counts", sa.JSON(), nullable=False),
        sa.Column("row_errors", sa.JSON(), nullable=False),
        sa.Column("failure_summary", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["source_id"], ["sources.source_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.business_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["mapping_version_id"], ["source_mappings.mapping_id"]),
        sa.ForeignKeyConstraint(["requested_by"], ["users.user_id"]),
        sa.PrimaryKeyConstraint("sync_id"),
        sa.UniqueConstraint("source_id", "idempotency_key", name="uq_sync_job_source_idempotency"),
    )
    op.create_index("ix_sync_jobs_source_id", "sync_jobs", ["source_id"])
    op.create_index("ix_sync_jobs_business_id", "sync_jobs", ["business_id"])
    op.create_index("ix_sync_jobs_correlation_id", "sync_jobs", ["correlation_id"])
    op.create_index("ix_sync_jobs_next_retry_at", "sync_jobs", ["next_retry_at"])


def downgrade() -> None:
    op.drop_index("ix_sync_jobs_next_retry_at", table_name="sync_jobs")
    op.drop_index("ix_sync_jobs_correlation_id", table_name="sync_jobs")
    op.drop_index("ix_sync_jobs_business_id", table_name="sync_jobs")
    op.drop_index("ix_sync_jobs_source_id", table_name="sync_jobs")
    op.drop_table("sync_jobs")
