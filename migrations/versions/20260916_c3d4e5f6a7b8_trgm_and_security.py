"""Trigram + security hardening indexes (Phase 12 fix).

Revision ID: c3d4e5f6a7b8
Revises: f0a1b2c3d4e5
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: str = "f0a1b2c3d4e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Enable pg_trgm extension for trigram search (product name search)
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # GIN trigram index for product name ilike searches (fixes ilike perf)
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_products_name_trgm
        ON products USING gin (name gin_trgm_ops)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_products_sku_trgm
        ON products USING gin (sku gin_trgm_ops)
        """
    )

    # Composite index for business_id + lifecycle_state (common filter)
    op.create_index(
        "ix_products_business_lifecycle",
        "products",
        ["business_id", "lifecycle_state"],
        if_not_exists=True,
    )

    # Index for sync_jobs business_id + status (scheduler/recovery queries)
    op.create_index(
        "ix_sync_jobs_business_status",
        "sync_jobs",
        ["business_id", "status"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_sync_jobs_business_status", table_name="sync_jobs", if_exists=True)
    op.drop_index("ix_products_business_lifecycle", table_name="products", if_exists=True)
    op.execute("DROP INDEX IF EXISTS ix_products_sku_trgm")
    op.execute("DROP INDEX IF EXISTS ix_products_name_trgm")
    # Don't drop extension in downgrade (may be used elsewhere)
