"""Business use cases (tenant context).

A Business is the durable tenant context. Creating one always creates the
creator's ACTIVE OWNER membership in the same transaction (single-owner
invariant at creation; ownership transfer is a separate high-risk workflow
added later). Cross-tenant access fails closed and reports 404 (never
reveal whether a business exists).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application.audit import AuditService
from app.application.authorization import get_active_membership
from app.domain import enums
from app.domain.errors import BusinessNotAccessible, ValidationError
from app.infrastructure.db.models import Business, BusinessType, Membership, User


def _utcnow() -> datetime:
    return datetime.now(UTC)


def list_business_types(db: Session) -> list[BusinessType]:
    rows = db.scalars(select(BusinessType).where(BusinessType.is_active.is_(True))).all()
    return list(rows)


def create_business(
    db: Session, *, user: User, name: str, business_type_key: str
) -> Business:
    """Create a Business and the creator's ACTIVE OWNER membership."""
    name = name.strip()
    if not (1 <= len(name) <= 160):
        raise ValidationError("Business name must be 1-160 characters")

    btype = db.get(BusinessType, business_type_key)
    if btype is None or not btype.is_active:
        raise ValidationError("Unknown or inactive business type")

    business = Business(business_name=name, business_type_key=business_type_key)
    db.add(business)
    db.flush()

    membership = Membership(
        user_id=user.user_id,
        business_id=business.business_id,
        role=enums.MembershipRole.OWNER.value,
        status=enums.MembershipStatus.ACTIVE.value,
        permissions=None,
        approved_by=user.user_id,
        approved_at=_utcnow(),
    )
    db.add(membership)
    db.flush()

    AuditService(db).record(
        action="business.created",
        actor_user_id=user.user_id,
        business_id=business.business_id,
        target_type="business",
        target_id=business.business_id,
    )
    return business


def get_business(db: Session, business_id: object) -> Business | None:
    return db.get(Business, business_id)


def require_business_access(
    db: Session, *, user: User, business_id: object
) -> tuple[Business, Membership]:
    """Resolve (business, viewer membership) or fail closed.

    Any failure mode (missing business, inactive business, no active
    membership) raises BusinessNotAccessible (404) — indistinguishable on
    purpose.
    """
    business = db.get(Business, business_id)
    membership = get_active_membership(db, user.user_id, business_id)
    if business is None or membership is None:
        raise BusinessNotAccessible()
    if business.lifecycle_state != enums.BusinessLifecycle.ACTIVE.value:
        raise BusinessNotAccessible()
    return business, membership


def list_businesses(db: Session, *, user: User) -> list[Business]:
    """Businesses where the user holds an ACTIVE membership."""
    rows = db.scalars(
        select(Business)
        .join(Membership, Membership.business_id == Business.business_id)
        .where(
            Membership.user_id == user.user_id,
            Membership.status == enums.MembershipStatus.ACTIVE.value,
        )
        .order_by(Business.created_at)
    ).all()
    return list(rows)
