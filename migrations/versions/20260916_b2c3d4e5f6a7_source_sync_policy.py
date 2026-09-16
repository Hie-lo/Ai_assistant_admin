"""Phase 8: configurable source scheduler policy.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sources",
        sa.Column("sync_interval_minutes", sa.Integer(), nullable=False, server_default="1440"),
    )
    op.add_column(
        "sources",
        sa.Column("automatic_sync_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("sources", "sync_interval_minutes", server_default=None)
    op.alter_column("sources", "automatic_sync_enabled", server_default=None)


def downgrade() -> None:
    op.drop_column("sources", "automatic_sync_enabled")
    op.drop_column("sources", "sync_interval_minutes")
