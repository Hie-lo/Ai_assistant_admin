"""Authoritative usage counters for entitlement limit keys (Phase 3).

Registration happens at import time; the app factory imports this module so
every process (API, worker) shares the same registry (spec section 6: checks
happen server-side right before execution, against real usage).

- ``products``: non-archived products of the business.
- ``sources``: active + paused sources (a paused source still occupies its
  seat; only a deleted source frees one — V1 has no deletion).
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select

from app.domain import entitlements, enums
from app.infrastructure.db import models


def _products_usage(db, business_id: uuid.UUID) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(models.Product)
            .where(
                models.Product.business_id == business_id,
                models.Product.lifecycle_state != enums.ProductLifecycle.ARCHIVED.value,
            )
        )
        or 0
    )


def _sources_usage(db, business_id: uuid.UUID) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(models.Source)
            .where(
                models.Source.business_id == business_id,
                models.Source.status.in_(
                    [enums.SourceStatus.ACTIVE.value, enums.SourceStatus.PAUSED.value]
                ),
            )
        )
        or 0
    )


def _channels_usage(db, business_id: uuid.UUID) -> int:
    # A slot is occupied until the connection is explicitly disconnected.
    return int(
        db.scalar(
            select(func.count())
            .select_from(models.PlatformConnection)
            .where(
                models.PlatformConnection.business_id == business_id,
                models.PlatformConnection.status
                != enums.PlatformConnectionStatus.DISCONNECTED.value,
            )
        )
        or 0
    )


entitlements.register_usage_counter("products", _products_usage)
entitlements.register_usage_counter("sources", _sources_usage)
entitlements.register_usage_counter("channels", _channels_usage)
