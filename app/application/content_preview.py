"""Content preview / render service (Phase 4 application layer).

Preview uses the SAME deterministic renderer that publication will use
(Post & Publication spec sections 7 and 23). Platform-specific rendering
arrives with the adapters in Phase 5; V1 output is platform-neutral text
+ media manifest + length diagnostics.

Preset resolution order (owner decision 2026-09-13):
1. product preset — when the product has one assigned AND the active
   plan includes ``product_preset_eligible`` AND it has an ACTIVE version;
   a downgrade or a missing preset degrades to the fallback below
   (non-destructive, warned + audited via preview warnings);
2. the business-type default preset (ACTIVE version);
3. a built-in minimal fallback (identity + price + stock + description),
   flagged with a warning so operators notice a missing preset.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application import entitlements as entsvc
from app.application.presets import (
    get_preset_version,
    resolve_default_preset,
)
from app.application.product_presets import (
    active_version as pp_active_version,
)
from app.application.product_presets import (
    get_product_preset,
    render_product_preset,
)
from app.domain import enums
from app.domain.content import (
    DEFAULT_MAX_MESSAGE_LENGTH,
    ContentBlock,
    RenderContext,
    RenderResult,
    render_blocks,
)
from app.domain.errors import NotFoundError
from app.infrastructure.db.models import (
    AIOutputArtifact,
    Business,
    Product,
    ProductMedia,
    ProductPresetVersion,
)

#: Built-in minimal fallback (only when no preset is configured at all).
_FALLBACK_BLOCKS: list[dict] = [
    {"id": "name", "type": "PRODUCT_FIELD", "ownership": "SYSTEM_MANAGED",
     "payload": {"field": "name"}},
    {"id": "price", "type": "STATIC_TEXT", "ownership": "SYSTEM_MANAGED",
     "payload": {"text": "قیمت: {price} {currency}"}},
    {"id": "stock", "type": "PRODUCT_FIELD", "ownership": "SYSTEM_MANAGED",
     "payload": {"field": "stock"}},
    {"id": "description", "type": "PRODUCT_FIELD", "ownership": "SYSTEM_MANAGED",
     "payload": {"field": "description"}},
]


def _get_product(db: Session, *, business: Business, product_id: uuid.UUID) -> Product:
    product = db.get(Product, product_id)
    if product is None or product.business_id != business.business_id:
        raise NotFoundError("product not found")
    return product


def _product_fields(product: Product) -> dict[str, str]:
    return {
        "name": product.name or "",
        "price": str(product.price) if product.price is not None else "",
        "currency": product.currency or "IRT",
        "stock": str(product.stock) if product.stock is not None else "",
        "category": product.category or "",
        "sku": product.sku or "",
        "barcode": product.barcode or "",
        "description": product.description or "",
        "hashtags": str((product.attributes or {}).get("hashtags") or ""),
        "created_at": (
            product.created_at.isoformat()[:10] if product.created_at else ""
        ),
    }


def _approved_ai(
    db: Session, *, business_id: uuid.UUID, product_id: uuid.UUID
) -> dict[str, str]:
    rows = db.scalars(
        select(AIOutputArtifact).where(
            AIOutputArtifact.business_id == business_id,
            AIOutputArtifact.product_id == product_id,
            AIOutputArtifact.status == enums.AIArtifactStatus.APPROVED.value,
        )
    ).all()
    out: dict[str, str] = {}
    for row in rows:
        out[row.output_definition_key] = (
            row.approved_value or row.generated_value or ""
        ).strip()
    return out


def _media_urls(db: Session, *, product_id: uuid.UUID) -> list[str]:
    rows = db.scalars(
        select(ProductMedia)
        .where(
            ProductMedia.product_id == product_id,
            ProductMedia.status != enums.MediaStatus.REMOVED.value,
        )
        .order_by(ProductMedia.position)
    ).all()
    return [m.url for m in rows if m.url]


def _context(
    db: Session, *, business: Business, product: Product, max_length: int | None = None
) -> RenderContext:
    return RenderContext(
        product_fields=_product_fields(product),
        attributes=dict(product.attributes or {}),
        ai_outputs=_approved_ai(
            db, business_id=business.business_id, product_id=product.product_id
        ),
        media_urls=_media_urls(db, product_id=product.product_id),
        max_length=max_length or DEFAULT_MAX_MESSAGE_LENGTH,
    )


def _assemble(
    *,
    result: RenderResult,
    warnings: list[str],
    source: str,
    preset_id: uuid.UUID | None,
    preset_name: str | None,
    version_used: int | None,
    product: Product,
    media_urls: list[str],
) -> dict:
    return {
        "product_id": product.product_id,
        "source": source,
        "preset_id": preset_id,
        "preset_name": preset_name,
        "preset_version": version_used,
        "text": result.text,
        "total_chars": result.total_chars,
        "max_length": result.max_length,
        "fits": result.fits,
        "blocked_reason": result.blocked_reason,
        "media_urls": media_urls,
        "blocks": [
            {
                "id": b.block_id,
                "type": b.type.value,
                "ownership": b.ownership.value,
                "priority": b.priority,
                "text": b.text,
                "kept": b.kept,
            }
            for b in result.blocks
        ],
        "warnings": warnings,
    }


def _pp_version(
    db: Session, preset, version_no: int | None
) -> ProductPresetVersion | None:
    if version_no is None:
        return pp_active_version(db, preset)
    return db.scalars(
        select(ProductPresetVersion).where(
            ProductPresetVersion.product_preset_id == preset.product_preset_id,
            ProductPresetVersion.version == version_no,
        )
    ).first()


def preview_product(
    db: Session,
    *,
    business: Business,
    product_id: uuid.UUID,
    preset_version: int | None = None,
    max_length: int | None = None,
) -> dict:
    product = _get_product(db, business=business, product_id=product_id)
    ent = entsvc.get_entitlements(db, business_id=business.business_id)
    warnings: list[str] = []
    media_urls = _media_urls(db, product_id=product.product_id)

    # 1) product preset (top-plan entitlement + assigned + active version)
    if product.product_preset_id is not None:
        eligible = bool(ent.has_active and ent.product_preset_eligible)
        if not eligible:
            warnings.append(
                "assigned product preset ignored: current plan does not include "
                "per-product presets (falling back to the default preset)"
            )
        else:
            try:
                pp = get_product_preset(
                    db,
                    business_id=business.business_id,
                    product_preset_id=product.product_preset_id,
                )
            except NotFoundError:
                pp = None
                warnings.append("assigned product preset no longer exists (fallback)")
            if pp is not None:
                ctx = _context(db, business=business, product=product, max_length=max_length)
                result = render_product_preset(
                    db, preset=pp, version_no=preset_version, ctx=ctx
                )
                av = _pp_version(db, pp, preset_version)
                return _assemble(
                    result=result,
                    warnings=[*warnings, *result.warnings],
                    source="product_preset",
                    preset_id=pp.product_preset_id,
                    preset_name=pp.name,
                    version_used=av.version if av else None,
                    product=product,
                    media_urls=media_urls,
                )

    # 2) business-type default preset
    resolved = resolve_default_preset(
        db, business_type_key=business.business_type_key
    )
    if resolved is not None:
        preset, version = resolved
        if preset_version is not None:
            version = get_preset_version(db, preset.preset_id, preset_version)
        ctx = _context(db, business=business, product=product, max_length=max_length)
        result = render_blocks(
            [ContentBlock.from_dict(b) for b in version.blocks], ctx
        )
        return _assemble(
            result=result,
            warnings=[*warnings, *result.warnings],
            source="business_type_default",
            preset_id=preset.preset_id,
            preset_name=preset.name,
            version_used=version.version,
            product=product,
            media_urls=media_urls,
        )

    # 3) built-in minimal fallback
    warnings.append(
        "no preset configured for this business type; minimal fallback used"
    )
    ctx = _context(db, business=business, product=product, max_length=max_length)
    result = render_blocks(
        [ContentBlock.from_dict(b) for b in _FALLBACK_BLOCKS], ctx
    )
    return _assemble(
        result=result,
        warnings=[*warnings, *result.warnings],
        source="fallback",
        preset_id=None,
        preset_name=None,
        version_used=None,
        product=product,
        media_urls=media_urls,
    )
