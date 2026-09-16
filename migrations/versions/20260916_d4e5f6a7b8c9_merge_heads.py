"""Merge multiple heads: notifications+sync_policy and trgm.

Revision ID: d4e5f6a7b8c9
Revises: b2c3d4e5f6a7, c3d4e5f6a7b8
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "d4e5f6a7b8c9"
down_revision: str | tuple[str, ...] = ("b2c3d4e5f6a7", "c3d4e5f6a7b8")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
