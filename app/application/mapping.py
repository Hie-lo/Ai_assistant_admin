"""Source column mapping: suggestion (heuristic) + versioning + activation.

Suggestion uses a normalized alias table (Persian + English). The owner
confirms/edits the draft; activation creates a mapping version. Old versions
are kept (SUPERSEDED) — changing a mapping never mutates an old version
(spec section 20: mapping is versioned).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.application.audit import AuditService
from app.domain import enums
from app.domain.identity import normalize_text
from app.infrastructure.db import models

# Canonical core fields the platform understands natively.
_STRING = enums.FieldType.STRING.value
_PRICE = enums.FieldType.PRICE.value
_STOCK = enums.FieldType.STOCK.value
_MEDIA = enums.FieldType.MEDIA_URL.value

CORE_FIELDS: dict[str, dict] = {
    "name": {"type": _STRING, "required": True, "display_name": "نام"},
    "description": {"type": _STRING, "required": False, "display_name": "توضیحات"},
    "price": {"type": _PRICE, "required": False, "display_name": "قیمت"},
    "currency": {"type": _STRING, "required": False, "display_name": "ارز"},
    "stock": {"type": _STOCK, "required": False, "display_name": "موجودی"},
    "category": {"type": _STRING, "required": False, "display_name": "دسته‌بندی"},
    "external_id": {"type": _STRING, "required": False, "display_name": "شناسه منبع"},
    "sku": {"type": _STRING, "required": False, "display_name": "کد کالا"},
    "barcode": {"type": _STRING, "required": False, "display_name": "بارکد"},
    "image1": {"type": _MEDIA, "required": False, "display_name": "تصویر ۱"},
    "image2": {"type": _MEDIA, "required": False, "display_name": "تصویر ۲"},
    "image3": {"type": _MEDIA, "required": False, "display_name": "تصویر ۳"},
}

# Normalized aliases -> canonical field. First hit wins.
_ALIAS_TABLE: list[tuple[str, str]] = [
    ("external id", "external_id"),
    ("external_id", "external_id"),
    ("source id", "external_id"),
    ("id source", "external_id"),
    ("شناسه منبع", "external_id"),
    ("شناسه‌ی منبع", "external_id"),
    ("شناسه", "external_id"),
    ("id", "external_id"),
    ("sku", "sku"),
    ("item code", "sku"),
    ("کد کالا", "sku"),
    ("کد محصول", "sku"),
    ("کد", "sku"),
    ("code", "sku"),
    ("barcode", "barcode"),
    ("بارکد", "barcode"),
    ("بارکد کالا", "barcode"),
    ("ean", "barcode"),
    ("gtin", "barcode"),
    ("price (toman)", "price"),
    ("price_toman", "price"),
    ("قیمت تومان", "price"),
    ("قیمت (تومان)", "price"),
    ("price", "price"),
    ("قیمت", "price"),
    ("currency", "currency"),
    ("ارز", "currency"),
    ("واحد پول", "currency"),
    ("stock", "stock"),
    ("موجودی", "stock"),
    ("inventory", "stock"),
    ("تعداد", "stock"),
    ("count", "stock"),
    ("category", "category"),
    ("cat", "category"),
    ("گروه", "category"),
    ("دسته", "category"),
    ("دسته بندی", "category"),
    ("category name", "category"),
    ("description", "description"),
    ("desc", "description"),
    ("details", "description"),
    ("توضیحات", "description"),
    ("شرح", "description"),
    ("name", "name"),
    ("title", "name"),
    ("product name", "name"),
    ("item name", "name"),
    ("product", "name"),
    ("item", "name"),
    ("محصول", "name"),
    ("نام محصول", "name"),
    ("نام", "name"),
    ("عنوان", "name"),
    ("main image", "image1"),
    ("image1", "image1"),
    ("image 1", "image1"),
    ("image", "image1"),
    ("photo", "image1"),
    ("تصویر 1", "image1"),
    ("تصویر یک", "image1"),
    ("عکس 1", "image1"),
    ("عکس اصلی", "image1"),
    ("تصویر", "image1"),
    ("عکس", "image1"),
    ("تصویر محصول", "image1"),
    ("عکس محصول", "image1"),
    ("image2", "image2"),
    ("image 2", "image2"),
    ("secondary image", "image2"),
    ("تصویر 2", "image2"),
    ("تصویر دوم", "image2"),
    ("عکس 2", "image2"),
    ("عکس دوم", "image2"),
    ("image3", "image3"),
    ("image 3", "image3"),
    ("third image", "image3"),
    ("تصویر 3", "image3"),
    ("تصویر سوم", "image3"),
    ("عکس 3", "image3"),
    ("عکس سوم", "image3"),
]


@dataclass(frozen=True)
class SuggestedEntry:
    column: str
    canonical_field: str | None  # None => proposed as CUSTOM
    field_kind: str
    field_type: str
    display_name: str
    required: bool
    template_exposed: bool
    confidence: float
    evidence: str


def suggest_entries(headers: list[str]) -> list[SuggestedEntry]:
    """Heuristic suggestion: normalized exact-alias match first, then
    containment (header contains alias). One column -> at most one core
    field; each core field is consumed at most once."""
    taken: set[str] = set()
    entries: list[SuggestedEntry] = []
    for column in headers:
        n = normalize_text(column)
        if not n:
            continue
        field = _ALIAS_TABLE_LOOKUP.get(n)
        if field and field not in taken:
            meta = CORE_FIELDS[field]
            entries.append(
                SuggestedEntry(
                    column=column,
                    canonical_field=field,
                    field_kind=enums.FieldKind.CORE.value,
                    field_type=meta["type"],
                    display_name=meta["display_name"],
                    required=meta["required"],
                    template_exposed=False,
                    confidence=0.9,
                    evidence="exact alias match",
                )
            )
            taken.add(field)
            continue
        entries.append(
            SuggestedEntry(
                column=column,
                canonical_field=None,
                field_kind=enums.FieldKind.CUSTOM.value,
                field_type=enums.FieldType.STRING.value,
                display_name=column,
                required=False,
                template_exposed=False,
                confidence=0.0,
                evidence="unmapped column -> proposed custom field",
            )
        )
    return entries


# Precompute normalized alias lookup (module import time).
_ALIAS_TABLE_LOOKUP = {normalize_text(a): field for a, field in _ALIAS_TABLE}


def entries_to_records(suggested: list[SuggestedEntry]) -> list[dict]:
    """Serialize suggestions into mapping entry dicts (editable JSON)."""
    out = []
    for s in suggested:
        out.append(
            {
                "column": s.column,
                "canonical_field": s.canonical_field,
                "field_kind": s.field_kind,
                "field_type": s.field_type,
                "display_name": s.display_name,
                "required": s.required,
                "template_exposed": s.template_exposed,
                "confidence": s.confidence,
                "evidence": s.evidence,
            }
        )
    return out


def create_mapping_version(
    db: Session,
    *,
    source: models.Source,
    entries: list[dict],
    actor_id: uuid.UUID,
) -> models.SourceMapping:
    """Create a NEW mapping version for the source (DRAFT or ACTIVE by flag)."""
    next_version = db.scalar(
        select(func.max(models.SourceMapping.version)).where(
            models.SourceMapping.source_id == source.source_id
        )
    ) or 0
    mapping = models.SourceMapping(
        source_id=source.source_id,
        business_id=source.business_id,
        version=int(next_version) + 1,
        status=enums.MappingStatus.DRAFT.value,
        entries=entries,
        created_by=actor_id,
    )
    db.add(mapping)
    db.flush()
    return mapping


def activate_mapping(
    db: Session,
    *,
    source: models.Source,
    mapping: models.SourceMapping,
    actor_id: uuid.UUID,
) -> models.SourceMapping:
    """Activate a mapping version: supersede the old active one, flip the
    source to ACTIVE (first activation moves it out of PENDING_MAPPING)."""
    for old in db.scalars(
        select(models.SourceMapping).where(
            models.SourceMapping.source_id == source.source_id,
            models.SourceMapping.status == enums.MappingStatus.ACTIVE.value,
        )
    ):
        old.status = enums.MappingStatus.SUPERSEDED.value
    mapping.status = enums.MappingStatus.ACTIVE.value
    source.status = enums.SourceStatus.ACTIVE.value
    AuditService(db).record(
        action="source.mapping.activated",
        actor_user_id=actor_id,
        business_id=source.business_id,
        target_type="source_mapping",
        target_id=str(mapping.mapping_id),
        meta={"version": mapping.version, "source_id": str(source.source_id)},
    )
    db.flush()
    return mapping


def active_mapping_for(db: Session, source: models.Source) -> models.SourceMapping | None:
    return db.scalar(
        select(models.SourceMapping).where(
            models.SourceMapping.source_id == source.source_id,
            models.SourceMapping.status == enums.MappingStatus.ACTIVE.value,
        )
    )


def entry_for_column(mapping: models.SourceMapping, column: str) -> dict | None:
    for e in mapping.entries:
        if e.get("column") == column:
            return e
    return None
