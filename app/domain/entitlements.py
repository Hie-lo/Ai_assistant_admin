"""Pure domain logic for the subscription / entitlement layer.

Framework-agnostic: state-transition table, entitlement derivation, usage
counter registry, and calendar arithmetic. ORM/HTTP layers depend on this,
never the other way round (keeps the rules unit-testable without a DB).

Sources:
- SUBSCRIPTION_PAYMENT_ENTITLEMENT_SPECIFICATION_V1 (states, separation,
  entitlement checks, expiry, upgrade/downgrade, credit pools)
- USER_BUSINESS_MEMBERSHIP_RBAC_SPEC_V1 section 20 (effective permission =
  role permission  business policy  subscription entitlement  scope)
"""

from __future__ import annotations

import calendar
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from app.domain.enums import SubscriptionStatus

# --- Subscription state machine -------------------------------------------

#: Explicit, auditable transitions (spec section 4). Terminal states
#: (EXPIRED/CANCELLED/REFUNDED) never transition further: a new purchase is
#: a new subscription record, preserving the history.
ALLOWED_TRANSITIONS: dict[SubscriptionStatus, frozenset[SubscriptionStatus]] = {
    SubscriptionStatus.PENDING: frozenset(
        {SubscriptionStatus.ACTIVE, SubscriptionStatus.CANCELLED}
    ),
    SubscriptionStatus.ACTIVE: frozenset(
        {
            SubscriptionStatus.GRACE,
            SubscriptionStatus.SUSPENDED,
            SubscriptionStatus.CANCELLED,
            SubscriptionStatus.REFUNDED,
        }
    ),
    SubscriptionStatus.GRACE: frozenset(
        {
            SubscriptionStatus.ACTIVE,
            SubscriptionStatus.EXPIRED,
            SubscriptionStatus.SUSPENDED,
            SubscriptionStatus.CANCELLED,
            SubscriptionStatus.REFUNDED,
        }
    ),
    SubscriptionStatus.SUSPENDED: frozenset(
        {
            SubscriptionStatus.ACTIVE,
            SubscriptionStatus.GRACE,
            SubscriptionStatus.CANCELLED,
            SubscriptionStatus.REFUNDED,
        }
    ),
    SubscriptionStatus.EXPIRED: frozenset(),
    SubscriptionStatus.CANCELLED: frozenset(),
    SubscriptionStatus.REFUNDED: frozenset(),
}


def can_transition(current: SubscriptionStatus, target: SubscriptionStatus) -> bool:
    """Whether ``current -> target`` is an allowed explicit transition."""
    return target in ALLOWED_TRANSITIONS.get(current, frozenset())


def is_terminal(status: SubscriptionStatus) -> bool:
    return not ALLOWED_TRANSITIONS.get(status, frozenset())


def is_live(status: SubscriptionStatus) -> bool:
    """Live = holds the business's single-concurrent slot."""
    return status in (
        SubscriptionStatus.PENDING,
        SubscriptionStatus.ACTIVE,
        SubscriptionStatus.GRACE,
        SubscriptionStatus.SUSPENDED,
    )


# --- Calendar arithmetic ----------------------------------------------------

_BILLING_PERIOD_MONTHS: dict[str, int] = {"monthly": 1}


def period_months(billing_period: str) -> int:
    """Months covered by one billing period (V1 supports monthly)."""
    try:
        return _BILLING_PERIOD_MONTHS[billing_period]
    except KeyError:
        raise ValueError(f"Unsupported billing period: {billing_period!r}") from None


def add_months(dt: datetime, months: int) -> datetime:
    """Add calendar months, clamping the day (Jan 31 + 1m -> Feb 28/29).

    Clamping (rather than overflow into the next month) keeps billing
    periods predictable and prevents "silent" extra days.
    """
    total = dt.month - 1 + months
    year = dt.year + total // 12
    month = total % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def period_label(start: datetime) -> str:
    """Monthly pool/period label, e.g. ``2026-09``."""
    return start.strftime("%Y-%m")


# --- Entitlements -----------------------------------------------------------


@dataclass(frozen=True)
class Entitlements:
    """The effective commercial limits for a business at one moment.

    Derived immediately before use from the business's current subscription
    (spec: checks happen server-side right before execution; UI visibility
    is not a security boundary).
    """

    has_active: bool
    subscription_status: str | None
    plan_code: str | None
    product_limit: int | None = None
    source_limit: int | None = None
    channel_limit: int | None = None
    sync_frequency_per_day: int | None = None
    ai_available: bool = False
    ai_monthly_credits: int = 0
    product_preset_eligible: bool = False
    preset_customization: str = "none"
    report_level: str = "none"
    media_storage_limit_bytes: int | None = None
    admin_seat_limit: int | None = None
    feature_flags: dict[str, object] = field(default_factory=dict)


def empty_entitlements() -> Entitlements:
    """No usable subscription: nothing is commercially permitted."""
    return Entitlements(
        has_active=False,
        subscription_status=None,
        plan_code=None,
    )


# --- Usage registry ---------------------------------------------------------
#
# Limit keys map to plan limit attributes; a *usage counter* for a key is
# registered by the owning domain (products in Phase 3, sources in Phase 3,
# channel links in Phase 5...). Until a counter is registered, usage is 0 —
# the limit then passes trivially, which is correct: there is no usage yet.

UsageCounter = Callable[[object, uuid.UUID], int]  # (db, business_id) -> int

#: limit key -> Entitlements attribute name
LIMIT_KEYS: dict[str, str] = {
    "products": "product_limit",
    "sources": "source_limit",
    "channels": "channel_limit",
    "admin_seats": "admin_seat_limit",
    "media_storage": "media_storage_limit_bytes",
}

_usage_counters: dict[str, UsageCounter] = {}


def register_usage_counter(key: str, counter: UsageCounter) -> None:
    """Register the authoritative usage counter for a limit key.

    Registration happens at domain-module import time, so every worker and
    the API process share the same registry.
    """
    if key not in LIMIT_KEYS:
        raise ValueError(f"Unknown limit key: {key!r}")
    _usage_counters[key] = counter


def usage_count(key: str, db: object, business_id: uuid.UUID) -> int:
    """Current usage for a limit key (0 if nothing is registered yet)."""
    counter = _usage_counters.get(key)
    if counter is None:
        return 0
    return int(counter(db, business_id))


def check_limit(entitlements: Entitlements, key: str, usage: int) -> bool:
    """Whether ``usage`` fits under the plan limit for ``key``.

    ``None`` limit = unlimited. A non-active subscription denies everything
    that requires a limit (checked by callers via ``has_active``).
    """
    limit = getattr(entitlements, LIMIT_KEYS[key])
    if limit is None:
        return True
    return usage < limit


def feature_enabled(entitlements: Entitlements, flag: str) -> bool:
    return bool(entitlements.feature_flags.get(flag, False))
