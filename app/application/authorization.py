"""Authorization evaluation (business-scoped, server-side).

Effective access = role permission ∩ business policy ∩ entitlement ∩ scope.
In Phase 1 the implemented dimensions are role permission and business
scope; entitlement checks are added with the Subscription phase (they gate
the same operations from another layer, never replacing this one).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import enums
from app.domain.permissions import resolve_profile
from app.infrastructure.db.models import Membership


def get_active_membership(db: Session, user_id: object, business_id: object) -> Membership | None:
    return db.scalar(
        select(Membership).where(
            Membership.user_id == user_id,
            Membership.business_id == business_id,
            Membership.status == enums.MembershipStatus.ACTIVE.value,
        )
    )


def has_permission(
    db: Session,
    *,
    user_id: object,
    business_id: object,
    permission: str,
) -> bool:
    membership = get_active_membership(db, user_id, business_id)
    if membership is None:
        return False
    if membership.role == enums.MembershipRole.OWNER.value:
        return True
    profile = resolve_profile(enums.MembershipRole.ADMIN, membership.permissions)
    return permission in profile
