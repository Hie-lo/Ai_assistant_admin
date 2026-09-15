"""Billing routes (Phase 2): plan catalog, subscription lifecycle, payments,
credits.

Two actor classes:
- Business members (owner permissions ``subscription.manage`` /
  ``business.view``) on business-scoped endpoints.
- Platform operator (``is_super_admin``) on plan catalog, payment
  verification, suspend/reactivate/refund, and credit top-ups.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.application import credits as credits_use
from app.application import entitlements as entitlements_use
from app.application import subscription as subscription_use
from app.application.authorization import has_permission
from app.application.business import require_business_access
from app.domain import enums
from app.domain.errors import BusinessNotAccessible, SubscriptionNotFound, ValidationError
from app.domain.permissions import BUSINESS_VIEW, SUBSCRIPTION_MANAGE
from app.infrastructure.db.models import Payment, Plan, Subscription
from app.interfaces.http import schemas
from app.interfaces.http.deps import (
    AuthorizationError,
    CurrentUser,
    Db,
    SuperAdmin,
    require_business_permission,
)

router = APIRouter(prefix="/api/v1", tags=["billing"])

BusinessViewScoped = Annotated[object, Depends(require_business_permission(BUSINESS_VIEW))]
BusinessSubscriptionScoped = Annotated[
    object, Depends(require_business_permission(SUBSCRIPTION_MANAGE))
]


def _subscription_out(db, sub: Subscription) -> schemas.SubscriptionOut:
    plan = db.get(Plan, sub.plan_id)
    pending_plan = db.get(Plan, sub.pending_plan_id) if sub.pending_plan_id else None
    out = schemas.SubscriptionOut.model_validate(sub)
    out.plan_code = plan.code if plan else None
    out.pending_plan_code = pending_plan.code if pending_plan else None
    return out


def _pending_payment(db, subscription_id: uuid.UUID):
    return db.scalar(
        select(Payment).where(
            Payment.subscription_id == subscription_id,
            Payment.status == enums.PaymentStatus.PENDING.value,
        )
    )


def _require_business_subscription(db, user, subscription_id: uuid.UUID, permission: str):
    """Owner-side subscription scoping: resolve sub, fail closed (404)."""
    sub = db.get(Subscription, subscription_id)
    if sub is None:
        raise BusinessNotAccessible()
    business, _membership = require_business_access(db, user=user, business_id=sub.business_id)
    if not has_permission(
        db, user_id=user.user_id, business_id=business.business_id, permission=permission
    ):
        raise AuthorizationError()
    return business, sub


def _require_operator_subscription(db, subscription_id: uuid.UUID) -> Subscription:
    sub = db.get(Subscription, subscription_id)
    if sub is None:
        raise SubscriptionNotFound()
    return sub


# --- Plan catalog (operator) ---


@router.get("/plans", response_model=list[schemas.PlanOut])
def list_plans(admin: SuperAdmin, db: Db, include_inactive: bool = False) -> list[schemas.PlanOut]:
    return [
        schemas.PlanOut.model_validate(p)
        for p in subscription_use.list_plans(db, include_inactive=include_inactive)
    ]


@router.post("/plans", response_model=schemas.PlanOut, status_code=201)
def create_plan(payload: schemas.PlanRequest, admin: SuperAdmin, db: Db) -> schemas.PlanOut:
    fields = payload.model_dump(exclude_none=True)
    if "code" not in fields or "name" not in fields:
        raise ValidationError("code and name are required to create a plan")
    plan = subscription_use.create_plan(db, actor=admin, fields=fields)
    return schemas.PlanOut.model_validate(plan)


@router.patch("/plans/{plan_id}", response_model=schemas.PlanOut)
def update_plan(
    plan_id: uuid.UUID, payload: schemas.PlanRequest, admin: SuperAdmin, db: Db
) -> schemas.PlanOut:
    fields = payload.model_dump(exclude_none=True)
    fields.pop("code", None)  # code is immutable
    plan = subscription_use.update_plan(db, actor=admin, plan_id=plan_id, fields=fields)
    return schemas.PlanOut.model_validate(plan)


# --- Business-facing subscription endpoints (owner permissions) ---


@router.get("/businesses/{business_id}/subscription", response_model=schemas.SubscriptionStatusOut)
def get_subscription_status(
    business_id: uuid.UUID,
    user: CurrentUser,
    db: Db,
    _scoped: BusinessViewScoped,
) -> schemas.SubscriptionStatusOut:
    current = subscription_use.get_current_subscription(db, business_id=business_id)
    latest = subscription_use.get_latest_subscription(db, business_id=business_id)
    ent = entitlements_use.get_entitlements(db, business_id=business_id)
    pending = _pending_payment(db, current.subscription_id) if current else None
    return schemas.SubscriptionStatusOut(
        subscription=_subscription_out(db, current) if current else None,
        latest=_subscription_out(db, latest) if latest else None,
        pending_payment=schemas.PaymentOut.model_validate(pending) if pending else None,
        entitlements=schemas.EntitlementsOut.model_validate(ent),
    )


@router.get("/businesses/{business_id}/subscriptions", response_model=list[schemas.SubscriptionOut])
def list_subscriptions(
    business_id: uuid.UUID,
    user: CurrentUser,
    db: Db,
    _scoped: BusinessViewScoped,
) -> list[schemas.SubscriptionOut]:
    return [
        _subscription_out(db, s)
        for s in subscription_use.list_subscriptions(db, business_id=business_id)
    ]


@router.post(
    "/businesses/{business_id}/subscriptions",
    response_model=schemas.SubscriptionOut,
    status_code=202,
)
def request_subscription(
    business_id: uuid.UUID,
    payload: schemas.SubscriptionRequest,
    user: CurrentUser,
    db: Db,
    _scoped: BusinessSubscriptionScoped,
) -> schemas.SubscriptionOut:
    business, _ = require_business_access(db, user=user, business_id=business_id)
    sub, _payment = subscription_use.request_subscription(
        db, business=business, plan_code=payload.plan_code, actor=user
    )
    return _subscription_out(db, sub)


@router.post("/subscriptions/{subscription_id}/cancel", response_model=schemas.SubscriptionOut)
def cancel_subscription(
    subscription_id: uuid.UUID, user: CurrentUser, db: Db
) -> schemas.SubscriptionOut:
    _business, sub = _require_business_subscription(
        db, user, subscription_id, SUBSCRIPTION_MANAGE
    )
    cancelled = subscription_use.cancel_subscription(
        db, subscription_id=subscription_id, actor=user
    )
    return _subscription_out(db, cancelled)


@router.get("/businesses/{business_id}/credits", response_model=schemas.CreditsOut)
def get_credits(
    business_id: uuid.UUID,
    user: CurrentUser,
    db: Db,
    _scoped: BusinessViewScoped,
) -> schemas.CreditsOut:
    return schemas.CreditsOut(
        pools=[
            schemas.CreditPoolOut.model_validate(p)
            for p in credits_use.list_pools(db, business_id=business_id)
        ],
        transactions=[
            schemas.CreditTransactionOut.model_validate(t)
            for t in credits_use.list_transactions(db, business_id=business_id)
        ],
    )


# --- Operator endpoints (platform super admin) ---


@router.post("/subscriptions/{subscription_id}/payments/verify")
def verify_payment(
    subscription_id: uuid.UUID,
    payload: schemas.PaymentVerifyRequest,
    admin: SuperAdmin,
    db: Db,
) -> dict:
    _sub = _require_operator_subscription(db, subscription_id)
    pending = _pending_payment(db, subscription_id)
    if pending is None:
        raise SubscriptionNotFound()
    sub2, payment = subscription_use.verify_payment(
        db,
        payment_id=pending.payment_id,
        amount_paid=payload.amount_paid,
        reference=payload.reference,
        note=payload.note,
        actor=admin,
    )
    return {
        "subscription": _subscription_out(db, sub2),
        "payment": schemas.PaymentOut.model_validate(payment),
    }


@router.post("/subscriptions/{subscription_id}/payments/reject")
def reject_payment(
    subscription_id: uuid.UUID,
    payload: schemas.PaymentRejectRequest,
    admin: SuperAdmin,
    db: Db,
) -> schemas.PaymentOut:
    _sub = _require_operator_subscription(db, subscription_id)
    pending = _pending_payment(db, subscription_id)
    if pending is None:
        raise SubscriptionNotFound()
    payment = subscription_use.reject_payment(
        db, payment_id=pending.payment_id, reason=payload.reason, actor=admin
    )
    return schemas.PaymentOut.model_validate(payment)


@router.post("/subscriptions/{subscription_id}/suspend", response_model=schemas.SubscriptionOut)
def suspend_subscription(
    subscription_id: uuid.UUID, payload: schemas.ReasonRequest, admin: SuperAdmin, db: Db
) -> schemas.SubscriptionOut:
    sub = subscription_use.suspend_subscription(
        db, subscription_id=subscription_id, reason=payload.reason, actor=admin
    )
    return _subscription_out(db, sub)


@router.post("/subscriptions/{subscription_id}/reactivate", response_model=schemas.SubscriptionOut)
def reactivate_subscription(
    subscription_id: uuid.UUID, admin: SuperAdmin, db: Db
) -> schemas.SubscriptionOut:
    sub = subscription_use.reactivate_subscription(db, subscription_id=subscription_id, actor=admin)
    return _subscription_out(db, sub)


@router.post("/subscriptions/{subscription_id}/refund", response_model=schemas.SubscriptionOut)
def refund_subscription(
    subscription_id: uuid.UUID, payload: schemas.ReasonRequest, admin: SuperAdmin, db: Db
) -> schemas.SubscriptionOut:
    sub = subscription_use.refund_subscription(
        db, subscription_id=subscription_id, reason=payload.reason, actor=admin
    )
    return _subscription_out(db, sub)


@router.post("/subscriptions/{subscription_id}/credits/topup", response_model=schemas.CreditPoolOut)
def topup_credits(
    subscription_id: uuid.UUID, payload: schemas.TopUpRequest, admin: SuperAdmin, db: Db
) -> schemas.CreditPoolOut:
    sub = _require_operator_subscription(db, subscription_id)
    pool = credits_use.topup_purchased_pool(
        db,
        business_id=sub.business_id,
        amount=payload.amount,
        note=payload.note,
        actor_user_id=admin.user_id,
    )
    return schemas.CreditPoolOut.model_validate(pool)
