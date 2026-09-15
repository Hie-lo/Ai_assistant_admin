"""Per-product preset service (Phase 4, business-scoped).

Owner decision 2026-09-13: per-product presets live in a COMPLETELY
separate section from business-type presets (own tables, own routes, own
permission) and are an entitlement of the TOP-PLAN only (plan field
``product_preset_eligible``).

Entitlement semantics (Phase 2 downgrade philosophy):
- creating/assigning requires an active plan with the flag;
- a downgrade does NOT delete assigned presets — the renderer simply
  falls back to the business-type default until eligibility returns
  (non-destructive, audited at render/preview time).
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application import entitlements as entsvc
from app.application.audit import AuditService
from app.application.presets import (
    _active_ai_keys,
    _content_hash,
)
from app.domain import enums
from app.domain.content import (
    ContentBlock,
    ContentError,
    RenderContext,
    render_blocks,
    validate_blocks,
)
from app.domain.errors import ConflictError, NotFoundError, ValidationError
from app.infrastructure.db.models import (
    Business,
    Product,
    ProductPreset,
    ProductPresetVersion,
    User,
)

_PP = "product_preset"


def _require_top_plan(db: Session, *, business_id: uuid.UUID):
    ent = entsvc.require_entitlement_unchecked(db, business_id=business_id)
    if not ent.product_preset_eligible:
        from app.domain.errors import EntitlementDenied

        raise EntitlementDenied(
            "per-product presets are exclusive to the top-tier plan"
        )
    return ent


def list_product_presets(db: Session, *, business_id: uuid.UUID) -> list[ProductPreset]:
    return list(
        db.scalars(
            select(ProductPreset)
            .where(ProductPreset.business_id == business_id)
            .order_by(ProductPreset.name)
        ).all()
    )


def get_product_preset(
    db: Session, *, business_id: uuid.UUID, product_preset_id: uuid.UUID
) -> ProductPreset:
    preset = db.get(ProductPreset, product_preset_id)
    if preset is None or preset.business_id != business_id:
        raise NotFoundError("product preset not found")
    return preset


def _versions(db: Session, product_preset_id: uuid.UUID) -> list[ProductPresetVersion]:
    return list(
        db.scalars(
            select(ProductPresetVersion)
            .where(ProductPresetVersion.product_preset_id == product_preset_id)
            .order_by(ProductPresetVersion.version)
        ).all()
    )


def active_version(db: Session, preset: ProductPreset) -> ProductPresetVersion | None:
    for v in _versions(db, preset.product_preset_id):
        if v.status == enums.PresetVersionStatus.ACTIVE.value:
            return v
    return None


def create_product_preset(
    db: Session,
    *,
    business: Business,
    actor: User,
    name: str,
    description: str | None,
    blocks: list[dict],
) -> ProductPreset:
    _require_top_plan(db, business_id=business.business_id)
    name = (name or "").strip()
    if not 1 <= len(name) <= 160:
        raise ValidationError("product preset name (1-160 chars) is required")
    try:
        parsed = validate_blocks(blocks, _active_ai_keys(db))
    except ContentError as exc:
        raise ValidationError(str(exc)) from exc
    existing = db.scalars(
        select(ProductPreset).where(
            ProductPreset.business_id == business.business_id,
            ProductPreset.name == name,
        )
    ).first()
    if existing is not None:
        raise ConflictError("a product preset with this name already exists")
    preset = ProductPreset(
        business_id=business.business_id,
        name=name,
        description=(description or "").strip() or None,
        created_by=actor.user_id,
    )
    db.add(preset)
    db.flush()
    version = ProductPresetVersion(
        product_preset_id=preset.product_preset_id,
        version=1,
        blocks=[b.to_dict() for b in parsed],
        content_hash=_content_hash([b.to_dict() for b in parsed]),
        status=enums.PresetVersionStatus.ACTIVE.value,
        created_by=actor.user_id,
    )
    db.add(version)
    db.flush()
    AuditService(db).record(
        action="product_preset.created",
        actor_user_id=actor.user_id,
        business_id=business.business_id,
        target_type=_PP,
        target_id=str(preset.product_preset_id),
    )
    return preset


def propose_product_preset_version(
    db: Session,
    *,
    business: Business,
    actor: User,
    product_preset_id: uuid.UUID,
    blocks: list[dict],
) -> ProductPresetVersion:
    preset = get_product_preset(
        db, business_id=business.business_id, product_preset_id=product_preset_id
    )
    _require_top_plan(db, business_id=business.business_id)
    try:
        parsed = validate_blocks(blocks, _active_ai_keys(db))
    except ContentError as exc:
        raise ValidationError(str(exc)) from exc
    versions = _versions(db, preset.product_preset_id)
    next_no = (versions[-1].version if versions else 0) + 1
    version = ProductPresetVersion(
        product_preset_id=preset.product_preset_id,
        version=next_no,
        blocks=[b.to_dict() for b in parsed],
        content_hash=_content_hash([b.to_dict() for b in parsed]),
        status=enums.PresetVersionStatus.DRAFT.value,
        created_by=actor.user_id,
    )
    db.add(version)
    db.flush()
    AuditService(db).record(
        action="product_preset.version_proposed",
        actor_user_id=actor.user_id,
        business_id=business.business_id,
        target_type=_PP,
        target_id=str(preset.product_preset_id),
        meta={"version": next_no},
    )
    return version


def activate_product_preset_version(
    db: Session,
    *,
    business: Business,
    actor: User,
    product_preset_id: uuid.UUID,
    version_no: int,
) -> ProductPresetVersion:
    preset = get_product_preset(
        db, business_id=business.business_id, product_preset_id=product_preset_id
    )
    _require_top_plan(db, business_id=business.business_id)
    version = db.scalars(
        select(ProductPresetVersion).where(
            ProductPresetVersion.product_preset_id == preset.product_preset_id,
            ProductPresetVersion.version == version_no,
        )
    ).first()
    if version is None:
        raise NotFoundError(f"product preset version {version_no} not found")
    if version.status != enums.PresetVersionStatus.DRAFT.value:
        raise ConflictError("only a DRAFT product preset version can be activated")
    for v in _versions(db, preset.product_preset_id):
        if v.status == enums.PresetVersionStatus.ACTIVE.value:
            v.status = enums.PresetVersionStatus.SUPERSEDED.value
    version.status = enums.PresetVersionStatus.ACTIVE.value
    AuditService(db).record(
        action="product_preset.version_activated",
        actor_user_id=actor.user_id,
        business_id=business.business_id,
        target_type=_PP,
        target_id=str(preset.product_preset_id),
        meta={"version": version_no},
    )
    return version


def assign_product_preset(
    db: Session,
    *,
    business: Business,
    actor: User,
    product_id: uuid.UUID,
    product_preset_id: uuid.UUID | None,
) -> Product:
    """Assign (or clear) the per-product preset for one product.

    Assigning requires top-plan entitlement; clearing never does
    (non-destructive downgrade behavior).
    """
    product = db.get(Product, product_id)
    if product is None or product.business_id != business.business_id:
        raise NotFoundError("product not found")
    if product_preset_id is not None:
        _require_top_plan(db, business_id=business.business_id)
        get_product_preset(
            db, business_id=business.business_id, product_preset_id=product_preset_id
        )
    previous = product.product_preset_id
    product.product_preset_id = product_preset_id
    db.flush()
    AuditService(db).record(
        action="product_preset.assigned"
        if product_preset_id is not None
        else "product_preset.unassigned",
        actor_user_id=actor.user_id,
        business_id=business.business_id,
        target_type="product",
        target_id=str(product.product_id),
        meta={
            "product_preset_id": str(product_preset_id) if product_preset_id else None,
            "previous": str(previous) if previous else None,
        },
    )
    return product


def render_product_preset(
    db: Session, *, preset: ProductPreset, version_no: int | None, ctx: RenderContext
):
    av = active_version(db, preset)
    version = av if version_no is None else next(
        (
            v
            for v in _versions(db, preset.product_preset_id)
            if v.version == version_no
        ),
        None,
    )
    if version is None:
        raise NotFoundError("product preset version not found")
    blocks = [ContentBlock.from_dict(b) for b in version.blocks]
    return render_blocks(blocks, ctx)
