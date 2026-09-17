"""Source column mapping: suggestion (heuristic) + versioning + activation.

Suggestion uses a normalized alias table (Persian + English). The owner
confirms/edits the draft; activation creates a mapping version. Old versions
are kept (SUPERSEDED) — changing a mapping never mutates an old version
(spec section 20: mapping is versioned).

Improved for real-world sheets: containment matching, more aliases,
Brand+Model fallback for name, Sale Price -> price, Status -> stock, etc.
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

# Normalized aliases -> canonical field. First hit wins for exact match,
# then containment matching tries to find alias inside header or vice versa.
_ALIAS_TABLE: list[tuple[str, str]] = [
    # external_id
    ("external id", "external_id"),
    ("external_id", "external_id"),
    ("source id", "external_id"),
    ("id source", "external_id"),
    ("شناسه منبع", "external_id"),
    ("شناسه‌ی منبع", "external_id"),
    ("شناسه", "external_id"),
    ("id", "external_id"),
    ("row", "external_id"),
    ("row id", "external_id"),
    ("row number", "external_id"),
    ("no", "external_id"),
    ("no.", "external_id"),
    ("number", "external_id"),
    ("#", "external_id"),
    ("ردیف", "external_id"),
    ("شماره", "external_id"),
    # sku
    ("sku", "sku"),
    ("item code", "sku"),
    ("کد کالا", "sku"),
    ("کد محصول", "sku"),
    ("کد", "sku"),
    ("code", "sku"),
    ("model", "sku"),  # Model often used as SKU for laptops
    ("مدل", "sku"),
    # barcode
    ("barcode", "barcode"),
    ("بارکد", "barcode"),
    ("بارکد کالا", "barcode"),
    ("ean", "barcode"),
    ("gtin", "barcode"),
    ("upc", "barcode"),
    # price - extensive real-world aliases
    ("price (toman)", "price"),
    ("price_toman", "price"),
    ("قیمت تومان", "price"),
    ("قیمت (تومان)", "price"),
    ("price", "price"),
    ("قیمت", "price"),
    ("sale price", "price"),
    ("sale_price", "price"),
    ("saleprice", "price"),
    ("selling price", "price"),
    ("قیمت فروش", "price"),
    ("قیمت فروش تومان", "price"),
    ("aed price", "price"),
    ("aed_price", "price"),
    ("price aed", "price"),
    ("usd price", "price"),
    ("final price", "price"),
    ("قیمت نهایی", "price"),
    ("cost", "price"),
    ("amount", "price"),
    ("مبلغ", "price"),
    # currency
    ("currency", "currency"),
    ("ارز", "currency"),
    ("واحد پول", "currency"),
    ("aed rate", "currency"),
    ("currency rate", "currency"),
    ("نرخ ارز", "currency"),
    # stock - including status
    ("stock", "stock"),
    ("موجودی", "stock"),
    ("inventory", "stock"),
    ("تعداد", "stock"),
    ("count", "stock"),
    ("qty", "stock"),
    ("quantity", "stock"),
    ("status", "stock"),
    ("وضعیت", "stock"),
    ("stock status", "stock"),
    ("availability", "stock"),
    ("موجود", "stock"),
    ("وضعیت موجودی", "stock"),
    # category
    ("category", "category"),
    ("cat", "category"),
    ("گروه", "category"),
    ("دسته", "category"),
    ("دسته بندی", "category"),
    ("category name", "category"),
    ("brand", "category"),
    ("برند", "category"),
    ("brand name", "category"),
    ("نام برند", "category"),
    ("type", "category"),
    ("نوع", "category"),
    # description
    ("description", "description"),
    ("desc", "description"),
    ("des", "description"),
    ("details", "description"),
    ("توضیحات", "description"),
    ("شرح", "description"),
    ("spec", "description"),
    ("specs", "description"),
    ("مشخصات", "description"),
    ("توضیح", "description"),
    # name - most important, many aliases
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
    ("product title", "name"),
    ("item title", "name"),
    ("full name", "name"),
    ("نام کامل", "name"),
    # image
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
    ("picture", "image1"),
    ("img", "image1"),
    ("image url", "image1"),
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
    """Heuristic suggestion with exact + containment matching.

    - Exact normalized alias match first (confidence 0.9)
    - Then containment: if header contains alias or alias contains header (confidence 0.7-0.8)
    - Special handling: Brand+Model fallback for name if no name found
    - One column -> at most one core field; each core field consumed at most once.
    - All columns become CUSTOM if not matched, but with better evidence.
    """
    taken: set[str] = set()
    entries: list[SuggestedEntry] = []
    # Pre-normalize all headers
    normalized_headers = [(col, normalize_text(col)) for col in headers]

    # First pass: exact matches
    for column, n in normalized_headers:
        if not n:
            # Empty header - still keep as CUSTOM to preserve index alignment
            entries.append(
                SuggestedEntry(
                    column=column,
                    canonical_field=None,
                    field_kind=enums.FieldKind.CUSTOM.value,
                    field_type=enums.FieldType.STRING.value,
                    display_name=column or "empty",
                    required=False,
                    template_exposed=False,
                    confidence=0.0,
                    evidence="empty header",
                )
            )
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
        else:
            # Placeholder for second pass
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

    # Second pass: containment matching for still-unmapped
    # Build list of alias -> field for containment
    for idx, (column, n) in enumerate(normalized_headers):
        if entries[idx].canonical_field is not None:
            continue  # already matched
        if not n:
            continue
        # Try containment: header contains alias or alias contains header
        best_field = None
        best_alias = None
        best_conf = 0.0
        for alias_norm, field in _ALIAS_TABLE_LOOKUP.items():
            if field in taken:
                continue
            # Skip very short aliases (like "no", "id", "#") for containment to avoid false positives
            if len(alias_norm) <= 2 and field in ("external_id", "sku"):
                continue
            if alias_norm in n or n in alias_norm:
                # Prefer longer alias matches and specific fields
                conf = 0.75
                # Boost confidence for price-related containment
                if field == "price" and ("price" in alias_norm or "قیمت" in alias_norm):
                    conf = 0.85
                elif field == "stock" and ("stock" in alias_norm or "status" in alias_norm or "موجود" in alias_norm):
                    conf = 0.8
                elif field == "description" and ("desc" in alias_norm or "توضیح" in alias_norm):
                    conf = 0.8
                if conf > best_conf:
                    best_conf = conf
                    best_field = field
                    best_alias = alias_norm
        if best_field:
            meta = CORE_FIELDS[best_field]
            entries[idx] = SuggestedEntry(
                column=column,
                canonical_field=best_field,
                field_kind=enums.FieldKind.CORE.value,
                field_type=meta["type"],
                display_name=meta["display_name"],
                required=meta["required"],
                template_exposed=False,
                confidence=best_conf,
                evidence=f"containment match: '{best_alias}' in '{n}'",
            )
            taken.add(best_field)

    # Third pass: if still no name, try Brand+Model fallback or first meaningful column as name
    has_name = any(e.canonical_field == "name" for e in entries)
    if not has_name and entries:
        # Look for Brand or Model columns to use as name
        brand_idx = None
        model_idx = None
        for idx, (col, n) in enumerate(normalized_headers):
            if "brand" in n or "برند" in n:
                brand_idx = idx
            if n == "model" or "model" in n or "مدل" in n:
                if model_idx is None:
                    model_idx = idx
        if brand_idx is not None and model_idx is not None:
            # Use Brand as name ONLY if we can generate Brand+Model later via extract_row
            # But to avoid duplicate names (Brand same for all rows), we should NOT map Brand to name
            # Instead, we leave Brand as category (already mapped) and let Model be name if possible
            # If Model not taken, use Model as name, Brand as category
            # This prevents all products named "Lenovo" -> duplicate detection
            if "name" not in taken:
                # Prefer Model as name (more unique) when Brand+Model exists
                if entries[model_idx].canonical_field is None:
                    entries[model_idx] = SuggestedEntry(
                        column=normalized_headers[model_idx][0],
                        canonical_field="name",
                        field_kind=enums.FieldKind.CORE.value,
                        field_type=_STRING,
                        display_name="نام",
                        required=True,
                        template_exposed=False,
                        confidence=0.75,
                        evidence="Brand+Model fallback: Model as name (unique)",
                    )
                    taken.add("name")
                else:
                    # Model already mapped to sku, use Brand+Model combined via Model column as name
                    # Keep Brand as category, but override Model's canonical to name if it's sku
                    # (sku can be regenerated from Model via custom attr)
                    if entries[model_idx].canonical_field == "sku":
                        entries[model_idx] = SuggestedEntry(
                            column=normalized_headers[model_idx][0],
                            canonical_field="name",
                            field_kind=enums.FieldKind.CORE.value,
                            field_type=_STRING,
                            display_name="نام",
                            required=True,
                            template_exposed=False,
                            confidence=0.72,
                            evidence="Brand+Model fallback: Model as name, Brand as category",
                        )
                        taken.discard("sku")
                        taken.add("name")
                # Brand stays as category if not already taken
                if brand_idx is not None and entries[brand_idx].canonical_field is None and "category" not in taken:
                    entries[brand_idx] = SuggestedEntry(
                        column=normalized_headers[brand_idx][0],
                        canonical_field="category",
                        field_kind=enums.FieldKind.CORE.value,
                        field_type=_STRING,
                        display_name="دسته‌بندی",
                        required=False,
                        template_exposed=False,
                        confidence=0.65,
                        evidence="Brand as category when Model is name",
                    )
                    taken.add("category")
        elif brand_idx is not None and "name" not in taken:
            # Only Brand exists, use it as name (better than nothing)
            entries[brand_idx] = SuggestedEntry(
                column=normalized_headers[brand_idx][0],
                canonical_field="name",
                field_kind=enums.FieldKind.CORE.value,
                field_type=_STRING,
                display_name="نام",
                required=True,
                template_exposed=False,
                confidence=0.6,
                evidence="Brand fallback as name",
            )
            taken.add("name")
        else:
            # Last resort: first column that looks like a product identifier as name
            for idx, e in enumerate(entries):
                if e.canonical_field is None:
                    col = normalized_headers[idx][0]
                    # Skip if column is clearly ID/Row/No
                    n = normalized_headers[idx][1]
                    if n in ("row", "no", "no.", "id", "number", "#", "ردیف"):
                        continue
                    entries[idx] = SuggestedEntry(
                        column=col,
                        canonical_field="name",
                        field_kind=enums.FieldKind.CORE.value,
                        field_type=_STRING,
                        display_name="نام",
                        required=True,
                        template_exposed=False,
                        confidence=0.5,
                        evidence="first meaningful column as name fallback",
                    )
                    taken.add("name")
                    break

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
