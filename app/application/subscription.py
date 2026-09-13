"""Subscription / payment lifecycle (Phase 2).

Rules implemented (SUBSCRIPTION_PAYMENT_ENTITLEMENT_SPECIFICATION_V1):
- Explicit, auditable state transitions only (domain state machine).
- Payment is a separate record; entitlement activation happens only when a
  payment is VERIFIED (manual verification by the platform operator —
  owner decision 2026-09-13).
- One live (non-terminal) subscription per business; one PENDING payment
  per subscription (service layer; Postgres partial unique indexes are the
  backstop, created in the migration).
- Expiry never corrupts history: terminal rows are kept, new writes are
  blocked via entitlements, data stays recoverable.
- Plan change (upgrade/downgrade) updates entitlements without touching
  historical data; a downgrade that exceeds usage is handled by the
  entitlement engine blocking new operations (spec section 8).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application import credits
from app.application.audit import AuditService
from app.domain import enums
from app.domain.entitlements import add_months, can_transition, period_label, period_months
from app.domain.errors import (
    PaymentAlreadySettled,
    PendingPaymentExists,
    PlanNotFound,
    SubscriptionNotFound,
    SubscriptionTransitionInvalid,
    ValidationError,
)
from app.infrastructure.db.models import Business, Payment, Plan, Subscription, User
from app.infrastructure.db.session import get_session_factory

_POSITIVE_INT_KEYS = (
    "product_limit",
    "source_limit",
    "channel_limit",
    "sync_frequency_per_day",
    "media_storage_limit_bytes",
    "admin_seat_limit",
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _as_aware(dt: datetime | None) -> datetime | None:
    """Normalize a DB datetime (naive on SQLite, aware on Postgres)."""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _period_delta(billing_period: str) -> timedelta:
    """Approximate calendar months for period_end computation.

    ``period_end`` is a billing bound, not a calendar computation: using
    months * 30 days keeps the rule deterministic across backends. The
    exact calendar-aware end is recomputed by ``add_months`` when a period
    is (re)started.
    """
    return timedelta(days=30 * period_months(billing_period))


# --- Plans (catalog) ---------------------------------------------------------


def list_plans(db: Session, *, include_inactive: bool = False) -> list[Plan]:
    stmt = select(Plan).order_by(Plan.created_at)
    if not include_inactive:
        stmt = stmt.where(Plan.is_active.is_(True))
    return list(db.scalars(stmt).all())


def get_plan(db: Session, *, code: str) -> Plan:
    plan = db.scalar(select(Plan).where(Plan.code == code.strip().lower()))
    if plan is None or not plan.is_active:
        raise PlanNotFound()
    return plan


def _valid_code_chars(code: str) -> bool:
    allowed = set("abcdefghijklmnopqrstuvwxyz0123456789_-")
    return all(c in allowed for c in code)


def create_plan(db: Session, *, actor: User, fields: dict) -> Plan:
    code = str(fields.get("code", "")).strip().lower()
    if not code or len(code) > 40 or not _valid_code_chars(code):
        raise ValidationError("Plan code must be 1-40 chars: a-z, 0-9, _ or -")
    if db.scalar(select(Plan).where(Plan.code == code)) is not None:
        raise ValidationError(f"Plan code already exists: {code}")
    name = str(fields.get("name", "")).strip()
    if not (1 <= len(name) <= 120):
        raise ValidationError("Plan name must be 1-120 characters")
    price = fields.get("price", 0)
    if not isinstance(price, int) or price < 0:
        raise ValidationError("price must be a non-negative integer")
    for key in _POSITIVE_INT_KEYS:
        value = fields.get(key)
        if value is not None and (not isinstance(value, int) or value <= 0):
            raise ValidationError(f"{key} must be a positive integer or null (unlimited)")
    ai_credits = fields.get("ai_monthly_credits", 0)
    if not isinstance(ai_credits, int) or ai_credits < 0:
        raise ValidationError("ai_monthly_credits must be a non-negative integer")

    plan = Plan(
        code=code,
        name=name,
        currency=str(fields.get("currency", "IRT")).strip()[:8] or "IRT",
        price=price,
        billing_period=str(fields.get("billing_period", "monthly")).strip().lower(),
        is_active=bool(fields.get("is_active", True)),
        product_limit=fields.get("product_limit"),
        source_limit=fields.get("source_limit"),
        channel_limit=fields.get("channel_limit"),
        sync_frequency_per_day=fields.get("sync_frequency_per_day"),
        ai_available=bool(fields.get("ai_available", False)),
        ai_monthly_credits=fields.get("ai_monthly_credits", 0),
        preset_customization=str(fields.get("preset_customization", "none")).strip().lower(),
        report_level=str(fields.get("report_level", "none")).strip().lower(),
        media_storage_limit_bytes=fields.get("media_storage_limit_bytes"),
        admin_seat_limit=fields.get("admin_seat_limit"),
        feature_flags=dict(fields.get("feature_flags") or {}),
    )
    if plan.billing_period not in ("monthly",):
        raise ValidationError("billing_period must be 'monthly' in V1")
    if plan.preset_customization not in ("none", "basic", "full"):
        raise ValidationError("preset_customization must be none|basic|full")
    if plan.report_level not in ("none", "basic", "full"):
        raise ValidationError("report_level must be none|basic|full")
    db.add(plan)
    db.flush()
    AuditService(db).record(
        action="plan.created",
        actor_user_id=actor.user_id,
        target_type="plan",
        target_id=plan.plan_id,
        meta={"code": plan.code, "price": plan.price, "currency": plan.currency},
    )
    return plan


def update_plan(db: Session, *, actor: User, plan_id: uuid.UUID, fields: dict) -> Plan:
    plan = db.get(Plan, plan_id)
    if plan is None:
        raise PlanNotFound()
    allowed = {
        "name",
        "currency",
        "price",
        "billing_period",
        "is_active",
        "product_limit",
        "source_limit",
        "channel_limit",
        "sync_frequency_per_day",
        "ai_available",
        "ai_monthly_credits",
        "preset_customization",
        "report_level",
        "media_storage_limit_bytes",
        "admin_seat_limit",
        "feature_flags",
    }
    changed: dict[str, object] = {}
    for key, value in fields.items():
        if key not in allowed:
            continue
        if key == "price" and (not isinstance(value, int) or value < 0):
            raise ValidationError("price must be a non-negative integer")
        if (
            key in _POSITIVE_INT_KEYS
            and value is not None
            and (not isinstance(value, int) or value <= 0)
        ):
            raise ValidationError(f"{key} must be a positive integer or null (unlimited)")
        if key == "ai_monthly_credits" and (not isinstance(value, int) or value < 0):
            raise ValidationError("ai_monthly_credits must be a non-negative integer")
        setattr(plan, key, value)
        changed[key] = value
    db.flush()
    AuditService(db).record(
        action="plan.updated",
        actor_user_id=actor.user_id,
        target_type="plan",
        target_id=plan.plan_id,
        meta={"code": plan.code, "changed": sorted(changed)},
    )
    return plan


# --- Subscription queries -----------------------------------------------------


def get_current_subscription(db: Session, *, business_id: uuid.UUID) -> Subscription | None:
    """The business's live (non-terminal) subscription, if any."""
    return db.scalar(
        select(Subscription)
        .where(
            Subscription.business_id == business_id,
            Subscription.status.in_(
                [s.value for s in enums.SUBSCRIPTION_NON_TERMINAL],
            ),
        )
        .order_by(Subscription.created_at.desc())
    )


def get_latest_subscription(db: Session, *, business_id: uuid.UUID) -> Subscription | None:
    """Most recent subscription row (for UI/history display)."""
    return db.scalar(
        select(Subscription)
        .where(Subscription.business_id == business_id)
        .order_by(Subscription.created_at.desc())
    )


def list_subscriptions(db: Session, *, business_id: uuid.UUID) -> list[Subscription]:
    return list(
        db.scalars(
            select(Subscription)
            .where(Subscription.business_id == business_id)
            .order_by(Subscription.created_at.desc())
        ).all()
    )


def _require_subscription(db: Session, subscription_id: uuid.UUID) -> Subscription:
    sub = db.get(Subscription, subscription_id)
    if sub is None:
        raise SubscriptionNotFound()
    return sub


def _require_transition(
    db: Session, sub: Subscription, target: enums.SubscriptionStatus, *, actor: User | None
) -> None:
    if not can_transition(enums.SubscriptionStatus(sub.status), target):
        AuditService(db).record(
            action="subscription.transition_invalid",
            outcome=enums.AuditOutcome.FAILURE,
            actor_user_id=actor.user_id if actor else None,
            business_id=sub.business_id,
            target_type="subscription",
            target_id=sub.subscription_id,
            meta={"from": sub.status, "to": target.value},
        )
        raise SubscriptionTransitionInvalid(
            f"Transition {sub.status} -> {target.value} is not allowed"
        )


def _plan_for(db: Session, sub: Subscription, *, pending: bool = False) -> Plan:
    plan_id = sub.pending_plan_id if (pending and sub.pending_plan_id) else sub.plan_id
    plan = db.get(Plan, plan_id)
    if plan is None:
        raise PlanNotFound()
    return plan


def _start_period(db: Session, sub: Subscription, plan: Plan, now: datetime) -> None:
    """Set a fresh billing period and grant its monthly credit pool."""
    sub.period_start = now
    sub.period_end = add_months(now, period_months(plan.billing_period))
    sub.grace_end = None
    credits.grant_monthly_pool(
        db,
        business_id=sub.business_id,
        period_label=period_label(now),
        amount=plan.ai_monthly_credits,
        actor_user_id=sub.updated_by,
    )


# --- Subscription lifecycle operations ----------------------------------------


def request_subscription(
    db: Session, *, business: Business, plan_code: str, actor: User
) -> tuple[Subscription, Payment]:
    """Owner: request a subscription (new purchase / renewal / plan change).

    Creates a PENDING subscription (new) or a PENDING payment on the live
    subscription (renewal / plan change). Activation requires operator
    verification of the manual payment.
    """
    plan = get_plan(db, code=plan_code)
    current = get_current_subscription(db, business_id=business.business_id)

    if current is None:
        sub = Subscription(
            business_id=business.business_id,
            plan_id=plan.plan_id,
            status=enums.SubscriptionStatus.PENDING.value,
            created_by=actor.user_id,
            updated_by=actor.user_id,
        )
        db.add(sub)
        db.flush()
        purpose = enums.PaymentPurpose.NEW
        audit_action = "subscription.requested"
    else:
        sub = current
        same_plan = plan.plan_id == sub.plan_id
        if not same_plan:
            sub.pending_plan_id = plan.plan_id
        purpose = enums.PaymentPurpose.RENEWAL if same_plan else enums.PaymentPurpose.PLAN_CHANGE
        audit_action = "subscription.change_requested"

    existing_payment = db.scalar(
        select(Payment).where(
            Payment.subscription_id == sub.subscription_id,
            Payment.status == enums.PaymentStatus.PENDING.value,
        )
    )
    if existing_payment is not None:
        raise PendingPaymentExists()

    payment = Payment(
        subscription_id=sub.subscription_id,
        business_id=business.business_id,
        purpose=purpose.value,
        status=enums.PaymentStatus.PENDING.value,
        expected_amount=plan.price,
        currency=plan.currency,
        initiated_by=actor.user_id,
    )
    db.add(payment)
    db.flush()
    AuditService(db).record(
        action=audit_action,
        actor_user_id=actor.user_id,
        business_id=business.business_id,
        target_type="subscription",
        target_id=sub.subscription_id,
        meta={"plan": plan.code, "purpose": purpose.value, "expected_amount": plan.price},
    )
    return sub, payment


def verify_payment(
    db: Session, *, payment_id: uuid.UUID, amount_paid: int | None, reference: str | None,
    note: str | None, actor: User,
) -> tuple[Subscription, Payment]:
    """Operator: verify a manual payment and activate/renew entitlements.

    The subscription row is locked (FOR UPDATE) so two concurrent verifiers
    cannot double-activate. An amount different from the expected price is
    accepted only with an explicit note (discrepancy is audited).
    """
    payment = db.scalar(
        select(Payment).where(Payment.payment_id == payment_id).with_for_update()
    )
    if payment is None:
        raise SubscriptionNotFound()  # no cross-tenant probing
    if payment.status != enums.PaymentStatus.PENDING.value:
        AuditService(db).record(
            action="payment.already_settled",
            outcome=enums.AuditOutcome.FAILURE,
            actor_user_id=actor.user_id,
            business_id=payment.business_id,
            target_type="payment",
            target_id=payment.payment_id,
        )
        raise PaymentAlreadySettled()

    sub = db.get(Subscription, payment.subscription_id, with_for_update=True)
    if sub is None:
        raise SubscriptionNotFound()
    plan = _plan_for(db, sub, pending=True)

    paid = payment.expected_amount if amount_paid is None else amount_paid
    if paid < 0:
        raise ValidationError("amount_paid must be non-negative")
    mismatch = paid != payment.expected_amount
    if mismatch and not (note and note.strip()):
        raise ValidationError("A note is required when the paid amount differs from expected")

    now = _utcnow()
    status = enums.SubscriptionStatus(sub.status)
    meta: dict[str, object] = {
        "purpose": payment.purpose,
        "paid": paid,
        "expected": payment.expected_amount,
        "reference": reference,
        "amount_mismatch": mismatch,
    }

    # Apply plan change first (affects which plan's period/credits apply).
    # A plan change is an ATTRIBUTES change, not a lifecycle transition;
    # the status transition (if any) is enforced by the purpose branch below.
    plan_changed = False
    if sub.pending_plan_id is not None:
        sub.plan_id = sub.pending_plan_id
        sub.pending_plan_id = None
        plan = _plan_for(db, sub)
        plan_changed = True

    purpose = enums.PaymentPurpose(payment.purpose)
    if purpose is enums.PaymentPurpose.NEW:
        _require_transition(db, sub, enums.SubscriptionStatus.ACTIVE, actor=actor)
        sub.status = enums.SubscriptionStatus.ACTIVE.value
        _start_period(db, sub, plan, now)
        meta["event"] = "activated"
    elif purpose is enums.PaymentPurpose.RENEWAL:
        if status is enums.SubscriptionStatus.ACTIVE:
            # Prepaid renewal: extend the current period; no status change.
            sub.period_end = add_months(
                _as_aware(sub.period_end) or now, period_months(plan.billing_period)
            )
            meta["event"] = "period_extended"
        else:
            _require_transition(db, sub, enums.SubscriptionStatus.ACTIVE, actor=actor)
            old_label = (
                period_label(sub.period_start) if _as_aware(sub.period_start) else None
            )
            sub.status = enums.SubscriptionStatus.ACTIVE.value
            _start_period(db, sub, plan, now)
            if old_label and old_label != period_label(now):
                credits.expire_monthly_pool(
                    db,
                    business_id=sub.business_id,
                    period_label=old_label,
                    actor_user_id=actor.user_id,
                )
            meta["event"] = "renewed"
    else:  # PLAN_CHANGE
        # Staying ACTIVE on a paid plan change is not a transition; only
        # moving in from GRACE/SUSPENDED is.
        if status is not enums.SubscriptionStatus.ACTIVE:
            _require_transition(db, sub, enums.SubscriptionStatus.ACTIVE, actor=actor)
        old_label = period_label(sub.period_start) if _as_aware(sub.period_start) else None
        sub.status = enums.SubscriptionStatus.ACTIVE.value
        _start_period(db, sub, plan, now)
        if old_label and old_label != period_label(now):
            credits.expire_monthly_pool(
                db, business_id=sub.business_id, period_label=old_label, actor_user_id=actor.user_id
            )
        meta["event"] = "plan_changed"
        meta["new_plan"] = plan.code

    sub.updated_by = actor.user_id
    payment.status = enums.PaymentStatus.VERIFIED.value
    payment.amount_paid = paid
    payment.reference = (reference or "").strip()[:120] or None
    payment.note = (note or "").strip()[:2000] or None
    payment.verified_by = actor.user_id
    payment.verified_at = now
    db.flush()

    AuditService(db).record(
        action="payment.verified",
        actor_user_id=actor.user_id,
        business_id=sub.business_id,
        target_type="payment",
        target_id=payment.payment_id,
        meta=meta,
    )
    if plan_changed:
        AuditService(db).record(
            action="subscription.plan_changed",
            actor_user_id=actor.user_id,
            business_id=sub.business_id,
            target_type="subscription",
            target_id=sub.subscription_id,
            meta={"plan": plan.code},
        )
    return sub, payment


def reject_payment(db: Session, *, payment_id: uuid.UUID, reason: str, actor: User) -> Payment:
    """Operator: reject a pending payment (subscription state unchanged)."""
    payment = db.scalar(
        select(Payment).where(Payment.payment_id == payment_id).with_for_update()
    )
    if payment is None:
        raise SubscriptionNotFound()
    if payment.status != enums.PaymentStatus.PENDING.value:
        raise PaymentAlreadySettled()
    payment.status = enums.PaymentStatus.REJECTED.value
    payment.verified_by = actor.user_id
    payment.verified_at = _utcnow()
    payment.note = (reason or "rejected").strip()[:2000]
    db.flush()
    AuditService(db).record(
        action="payment.rejected",
        actor_user_id=actor.user_id,
        business_id=payment.business_id,
        target_type="payment",
        target_id=payment.payment_id,
        meta={"reason": reason},
    )
    return payment


def cancel_subscription(db: Session, *, subscription_id: uuid.UUID, actor: User) -> Subscription:
    """Owner: cancel a PENDING subscription (before payment verification)."""
    sub = _require_subscription(db, subscription_id)
    _require_transition(db, sub, enums.SubscriptionStatus.CANCELLED, actor=actor)
    sub.status = enums.SubscriptionStatus.CANCELLED.value
    sub.updated_by = actor.user_id
    pending = db.scalar(
        select(Payment).where(
            Payment.subscription_id == sub.subscription_id,
            Payment.status == enums.PaymentStatus.PENDING.value,
        )
    )
    if pending is not None:
        pending.status = enums.PaymentStatus.REJECTED.value
        pending.verified_by = actor.user_id
        pending.verified_at = _utcnow()
        pending.note = "subscription cancelled"
    db.flush()
    AuditService(db).record(
        action="subscription.cancelled",
        actor_user_id=actor.user_id,
        business_id=sub.business_id,
        target_type="subscription",
        target_id=sub.subscription_id,
    )
    return sub


def suspend_subscription(
    db: Session, *, subscription_id: uuid.UUID, reason: str, actor: User
) -> Subscription:
    """Operator: manually suspend (entitlements blocked until reactivated)."""
    sub = _require_subscription(db, subscription_id)
    _require_transition(db, sub, enums.SubscriptionStatus.SUSPENDED, actor=actor)
    sub.status = enums.SubscriptionStatus.SUSPENDED.value
    sub.updated_by = actor.user_id
    db.flush()
    AuditService(db).record(
        action="subscription.suspended",
        actor_user_id=actor.user_id,
        business_id=sub.business_id,
        target_type="subscription",
        target_id=sub.subscription_id,
        meta={"reason": reason},
    )
    return sub


def reactivate_subscription(
    db: Session, *, subscription_id: uuid.UUID, actor: User
) -> Subscription:
    """Operator: reactivate a SUSPENDED subscription.

    Within its period -> ACTIVE; after its period -> GRACE (the owner still
    has the grace window to pay a renewal).
    """
    sub = _require_subscription(db, subscription_id)
    now = _utcnow()
    period_end = _as_aware(sub.period_end)
    if period_end is not None and period_end > now:
        _require_transition(db, sub, enums.SubscriptionStatus.ACTIVE, actor=actor)
        sub.status = enums.SubscriptionStatus.ACTIVE.value
        event = "reactivated"
    else:
        _require_transition(db, sub, enums.SubscriptionStatus.GRACE, actor=actor)
        sub.status = enums.SubscriptionStatus.GRACE.value
        sub.grace_end = now + timedelta(days=_grace_days())
        event = "grace_after_suspension"
    sub.updated_by = actor.user_id
    db.flush()
    AuditService(db).record(
        action="subscription.reactivated",
        actor_user_id=actor.user_id,
        business_id=sub.business_id,
        target_type="subscription",
        target_id=sub.subscription_id,
        meta={"event": event},
    )
    return sub


def refund_subscription(
    db: Session, *, subscription_id: uuid.UUID, reason: str, actor: User
) -> Subscription:
    """Operator: refund (terminal). Monthly credits expire; purchased
    credits remain (they were bought separately)."""
    sub = _require_subscription(db, subscription_id)
    _require_transition(db, sub, enums.SubscriptionStatus.REFUNDED, actor=actor)
    old_label = period_label(sub.period_start) if _as_aware(sub.period_start) else None
    sub.status = enums.SubscriptionStatus.REFUNDED.value
    sub.updated_by = actor.user_id
    if old_label:
        credits.expire_monthly_pool(
            db, business_id=sub.business_id, period_label=old_label, actor_user_id=actor.user_id
        )
    db.flush()
    AuditService(db).record(
        action="subscription.refunded",
        actor_user_id=actor.user_id,
        business_id=sub.business_id,
        target_type="subscription",
        target_id=sub.subscription_id,
        meta={"reason": reason},
    )
    return sub


def _grace_days() -> int:
    from app.config.settings import get_settings

    return get_settings().subscription_grace_days


# --- Scheduler ----------------------------------------------------------------


def process_subscription_cycles(db: Session, *, grace_days: int | None = None) -> dict[str, int]:
    """Move the world forward: ACTIVE past period -> GRACE; GRACE past grace -> EXPIRED.

    Safe to run repeatedly (idempotent). Each transition is audited with the
    scheduler correlation id. Returns counts for observability.
    """
    grace = _grace_days() if grace_days is None else grace_days
    now = _utcnow()
    grace_entered = 0
    expired = 0

    active_rows = db.scalars(
        select(Subscription)
        .where(Subscription.status == enums.SubscriptionStatus.ACTIVE.value)
        .with_for_update()
    ).all()
    for sub in active_rows:
        end = _as_aware(sub.period_end)
        if end is not None and end <= now:
            sub.status = enums.SubscriptionStatus.GRACE.value
            sub.grace_end = end + timedelta(days=grace)
            grace_entered += 1
            AuditService(db).record(
                action="subscription.grace_entered",
                business_id=sub.business_id,
                target_type="subscription",
                target_id=sub.subscription_id,
                meta={"period_end": end.isoformat(), "grace_end": sub.grace_end.isoformat()},
            )

    grace_rows = db.scalars(
        select(Subscription)
        .where(Subscription.status == enums.SubscriptionStatus.GRACE.value)
        .with_for_update()
    ).all()
    for sub in grace_rows:
        grace_end = _as_aware(sub.grace_end)
        if grace_end is not None and grace_end <= now:
            old_label = (
                period_label(sub.period_start) if _as_aware(sub.period_start) else None
            )
            sub.status = enums.SubscriptionStatus.EXPIRED.value
            expired += 1
            if old_label:
                credits.expire_monthly_pool(
                    db, business_id=sub.business_id, period_label=old_label
                )
            AuditService(db).record(
                action="subscription.expired",
                business_id=sub.business_id,
                target_type="subscription",
                target_id=sub.subscription_id,
                meta={"grace_end": grace_end.isoformat(), "pool_period": old_label},
            )

    if grace_entered or expired:
        db.flush()
    return {"grace_entered": grace_entered, "expired": expired}


def run_cycle_task() -> dict[str, int]:
    """Worker entry point (its own session; never request-scoped)."""
    factory = get_session_factory()
    session = factory()
    try:
        result = process_subscription_cycles(session)
        session.commit()
        return result
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
