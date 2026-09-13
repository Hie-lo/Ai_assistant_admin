"""Entitlement service (Phase 2 application layer).

Bridges the pure domain (app.domain.entitlements) and the database:
derives a business's effective entitlements from its current subscription
and enforces limits immediately before privileged operations.

Spec anchors:
- SUBSCRIPTION_PAYMENT_ENTITLEMENT_V1 sections 6 (server-side check right
  before execution), 7 (expiry must not corrupt data; block new writes),
  8 (downgrade may prevent new operations until usage is reduced).
- USER_BUSINESS_MEMBERSHIP_RBAC_V1 section 20 (effective permission =
  role  business policy  subscription entitlement  scope).
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.application import subscription as subsvc
from app.domain import enums
from app.domain.entitlements import (
    Entitlements,
    check_limit,
    empty_entitlements,
    feature_enabled,
    usage_count,
)
from app.domain.errors import EntitlementDenied
from app.infrastructure.db.models import Plan

#: Statuses under which a subscription grants entitlements.
_GRANTING_STATUSES = frozenset(
    {enums.SubscriptionStatus.ACTIVE.value, enums.SubscriptionStatus.GRACE.value}
)


def get_entitlements(db: Session, *, business_id: uuid.UUID) -> Entitlements:
    """Effective entitlements for a business, derived now."""
    sub = subsvc.get_current_subscription(db, business_id=business_id)
    if sub is None:
        return empty_entitlements()
    plan = db.get(Plan, sub.plan_id)
    if plan is None:
        return empty_entitlements()
    status = sub.status
    if status not in _GRANTING_STATUSES:
        # PENDING / SUSPENDED: the plan is known (UI) but nothing is granted.
        return Entitlements(
            has_active=False,
            subscription_status=status,
            plan_code=plan.code,
        )
    return Entitlements(
        has_active=True,
        subscription_status=status,
        plan_code=plan.code,
        product_limit=plan.product_limit,
        source_limit=plan.source_limit,
        channel_limit=plan.channel_limit,
        sync_frequency_per_day=plan.sync_frequency_per_day,
        ai_available=plan.ai_available,
        ai_monthly_credits=plan.ai_monthly_credits,
        preset_customization=plan.preset_customization,
        report_level=plan.report_level,
        media_storage_limit_bytes=plan.media_storage_limit_bytes,
        admin_seat_limit=plan.admin_seat_limit,
        feature_flags=dict(plan.feature_flags or {}),
    )


def require_entitlement(
    db: Session, *, business_id: uuid.UUID, limit_key: str
) -> Entitlements:
    """Block the operation unless the active subscription covers it.

    Raise this in the use case immediately before the expensive or
    externally visible side effect (spec section 6).
    """
    ent = get_entitlements(db, business_id=business_id)
    if not ent.has_active:
        raise EntitlementDenied(
            f"business has no active subscription (status: {ent.subscription_status or 'none'})"
        )
    usage = usage_count(limit_key, db, business_id)
    if not check_limit(ent, limit_key, usage):
        raise EntitlementDenied(
            f"plan limit reached for {limit_key} (usage {usage})"
        )
    return ent


def require_ai_available(db: Session, *, business_id: uuid.UUID) -> Entitlements:
    """Block AI work unless the active plan includes AI."""
    ent = require_entitlement_unchecked(db, business_id=business_id)
    if not ent.ai_available:
        raise EntitlementDenied("AI is not included in the active plan")
    return ent


def require_entitlement_unchecked(
    db: Session, *, business_id: uuid.UUID
) -> Entitlements:
    """Require an active subscription (no specific limit)."""
    ent = get_entitlements(db, business_id=business_id)
    if not ent.has_active:
        raise EntitlementDenied(
            f"business has no active subscription (status: {ent.subscription_status or 'none'})"
        )
    return ent


def feature_allowed(db: Session, *, business_id: uuid.UUID, flag: str) -> bool:
    """Whether a plan feature flag is enabled under the active subscription."""
    ent = get_entitlements(db, business_id=business_id)
    return ent.has_active and feature_enabled(ent, flag)
