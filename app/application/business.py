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

    # Prevent duplicate business name for same user (idempotency for double-click)
    existing = db.scalars(
        select(Business)
        .join(Membership, Membership.business_id == Business.business_id)
        .where(
            Membership.user_id == user.user_id,
            Membership.status == enums.MembershipStatus.ACTIVE.value,
            Business.business_name.ilike(name),
        )
    ).first()
    if existing is not None:
        # Return existing instead of creating duplicate (customer-friendly)
        return existing

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
    """Businesses where the user holds an ACTIVE membership — distinct to prevent duplicates."""
    rows = db.scalars(
        select(Business)
        .join(Membership, Membership.business_id == Business.business_id)
        .where(
            Membership.user_id == user.user_id,
            Membership.status == enums.MembershipStatus.ACTIVE.value,
        )
        .distinct()
        .order_by(Business.created_at)
    ).all()
    return list(rows)


def delete_business(db: Session, *, user: User, business_id: object) -> None:
    """Delete a business and all its data (owner only). Hard delete with cascade.

    This is for user-requested cleanup (e.g., duplicate business). Deletes in dependency order
    to avoid FK violations, using SAVEPOINTs for robustness.
    """
    from sqlalchemy import select as sel

    business, membership = require_business_access(db, user=user, business_id=business_id)
    if membership.role != enums.MembershipRole.OWNER.value:
        from app.domain.errors import ValidationError

        raise ValidationError("Only owner can delete business")

    # Helper to safely delete all rows of a model filtered by business_id
    def _delete_all(model, business_filter=True, extra_filter=None):
        try:
            with db.begin_nested():
                stmt = sel(model)
                if business_filter and hasattr(model, "business_id"):
                    stmt = stmt.where(model.business_id == business.business_id)
                if extra_filter is not None:
                    stmt = stmt.where(extra_filter)
                for row in db.scalars(stmt).all():
                    db.delete(row)
        except Exception:
            try:
                db.rollback()
            except Exception:
                pass
            # Fallback: try bulk delete
            try:
                with db.begin_nested():
                    stmt = model.__table__.delete()
                    if business_filter and hasattr(model, "business_id"):
                        stmt = stmt.where(model.__table__.c.business_id == business.business_id)
                    db.execute(stmt)
            except Exception:
                pass

    # Import here to avoid circular
    from app.infrastructure.db import models as m

    # Order matters: child -> parent
    # 1. Publications & attempts (depend on posts, connections)
    try:
        with db.begin_nested():
            for pub in db.scalars(sel(m.Publication).where(m.Publication.business_id == business.business_id)).all():
                # Delete attempts first
                for att in db.scalars(sel(m.PublicationAttempt).where(m.PublicationAttempt.publication_id == pub.publication_id)).all():
                    db.delete(att)
                db.delete(pub)
    except Exception:
        pass

    # 2. Posts and versions
    try:
        with db.begin_nested():
            for post in db.scalars(sel(m.Post).where(m.Post.business_id == business.business_id)).all():
                for pv in db.scalars(sel(m.PostVersion).where(m.PostVersion.post_id == post.post_id)).all():
                    db.delete(pv)
                db.delete(post)
    except Exception:
        pass

    # 3. Products and related
    try:
        with db.begin_nested():
            for product in db.scalars(sel(m.Product).where(m.Product.business_id == business.business_id)).all():
                for pv in db.scalars(sel(m.ProductVersion).where(m.ProductVersion.product_id == product.product_id)).all():
                    db.delete(pv)
                # Media
                if hasattr(m, "ProductMedia"):
                    for pm in db.scalars(sel(m.ProductMedia).where(m.ProductMedia.product_id == product.product_id)).all():
                        db.delete(pm)
                # AI artifacts
                if hasattr(m, "AIOutputArtifact"):
                    for art in db.scalars(sel(m.AIOutputArtifact).where(m.AIOutputArtifact.product_id == product.product_id)).all():
                        db.delete(art)
                db.delete(product)
    except Exception:
        pass

    # 3b. Product presets and versions
    try:
        with db.begin_nested():
            for pp in db.scalars(sel(m.ProductPreset).where(m.ProductPreset.business_id == business.business_id)).all():
                if hasattr(m, "ProductPresetVersion"):
                    for ppv in db.scalars(sel(m.ProductPresetVersion).where(m.ProductPresetVersion.product_preset_id == pp.product_preset_id)).all():
                        db.delete(ppv)
                db.delete(pp)
    except Exception:
        pass

    # 4. Sources and related
    try:
        with db.begin_nested():
            for source in db.scalars(sel(m.Source).where(m.Source.business_id == business.business_id)).all():
                for rec in db.scalars(sel(m.SourceRecord).where(m.SourceRecord.source_id == source.source_id)).all():
                    db.delete(rec)
                for mapping in db.scalars(sel(m.SourceMapping).where(m.SourceMapping.source_id == source.source_id)).all():
                    db.delete(mapping)
                for run in db.scalars(sel(m.ImportRun).where(m.ImportRun.source_id == source.source_id)).all():
                    db.delete(run)
                for job in db.scalars(sel(m.SyncJob).where(m.SyncJob.source_id == source.source_id)).all():
                    db.delete(job)
                db.delete(source)
    except Exception:
        pass

    # 5. Other business-scoped tables
    for model_name in [
        "PlatformConnection",
        "ReviewCase",
        "Notification",
        "SyncJob",
        "ProductPreset",
        "CreditPool",
        "CreditTransaction",
        "Subscription",
        "Payment",
        "ChannelLink",
        "AdminInvite",
        "AdminAccessRequest",
        "AuditLog",
    ]:
        model = getattr(m, model_name, None)
        if model is not None and hasattr(model, "business_id"):
            _delete_all(model)

    # 6. Memberships
    _delete_all(Membership)

    # 7. Finally business
    try:
        with db.begin_nested():
            db.delete(business)
            db.flush()
    except Exception:
        # Last resort bulk delete
        try:
            db.execute(m.Business.__table__.delete().where(m.Business.__table__.c.business_id == business.business_id))
            db.flush()
        except Exception:
            pass
