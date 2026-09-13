"""Reference-data seeding (single source of truth).

Used by the Alembic migrations AND by the test schema builder, so production
and tests never drift. The starter plan's price/limits are initial
placeholders: the owner tunes them through the plan-management endpoints
without code changes (Gate-approved plan-catalog behavior).
"""

from __future__ import annotations

import sqlalchemy as sa

#: Default business type (Phase 1).
BUSINESS_TYPE_SEED: dict[str, dict[str, object]] = {
    "general": {
        "key": "general",
        "name": "General business",
        "description": "Default business type.",
        "is_active": True,
    }
}

#: Starter plan (Phase 2). Prices in Toman; limits are initial values that
#: the operator can adjust at runtime.
STARTER_PLAN: dict[str, object] = {
    "code": "starter",
    "name": "Starter",
    "currency": "IRT",
    "price": 990_000,
    "billing_period": "monthly",
    "is_active": True,
    "product_limit": 100,
    "source_limit": 2,
    "channel_limit": 2,
    "sync_frequency_per_day": 3,
    "ai_available": True,
    "ai_monthly_credits": 50,
    "preset_customization": "basic",
    "report_level": "basic",
    "media_storage_limit_bytes": 1_073_741_824,  # 1 GiB
    "admin_seat_limit": 3,
    "feature_flags": {},
}


def seed_reference_data(conn: sa.Connection) -> None:
    """Insert reference data if absent (idempotent)."""
    from app.infrastructure.db.models import BusinessType, Plan

    existing_types = conn.execute(sa.select(BusinessType.key)).all()
    existing_keys = {row[0] for row in existing_types}
    for row in BUSINESS_TYPE_SEED.values():
        if row["key"] not in existing_keys:
            conn.execute(BusinessType.__table__.insert().values([row]))

    if conn.execute(sa.select(Plan.code).where(Plan.code == STARTER_PLAN["code"])).first() is None:
        conn.execute(Plan.__table__.insert().values([dict(STARTER_PLAN)]))

    conn.commit()
