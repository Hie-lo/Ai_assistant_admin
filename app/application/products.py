"""Product application service: manual edits, media ops, lifecycle,
identity candidates.

All operations are business-scoped (no cross-tenant access) and audited.
Source refresh is handled by the import pipeline; this module covers the
trusted internal view and the human side.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.application.audit import AuditService
from app.domain import change_risk, enums
from app.domain.identity import IdentityCandidate, normalize_text
from app.infrastructure.db import models

EDITABLE_FIELDS = (
    "name",
    "description",
    "price",
    "currency",
    "stock",
    "category",
    "external_id",
    "sku",
    "barcode",
    "attributes",
)


def get_product(db: Session, business_id: uuid.UUID, product_id: uuid.UUID) -> models.Product:
    """Business-scoped fetch; cross-tenant reads return None (404 upstream)."""
    return db.scalar(
        select(models.Product).where(
            models.Product.product_id == product_id,
            models.Product.business_id == business_id,
        )
    )


def list_products(
    db: Session,
    business_id: uuid.UUID,
    *,
    state: enums.ProductLifecycle | None = None,
    q: str | None = None,
) -> list[models.Product]:
    stmt = select(models.Product).where(models.Product.business_id == business_id)
    if state is not None:
        stmt = stmt.where(models.Product.lifecycle_state == state.value)
    else:
        # Archived products are hidden from the default catalog view.
        stmt = stmt.where(models.Product.lifecycle_state != enums.ProductLifecycle.ARCHIVED.value)
    if q:
        # Match raw OR normalized form (stored names keep the raw source
        # spelling; normalization handles Persian yae/kaf + case drift).
        patterns = {f"%{v}%" for v in (q, normalize_text(q)) if v}
        stmt = stmt.where(or_(*(models.Product.name.ilike(p) for p in patterns)))
    return list(db.scalars(stmt.order_by(models.Product.created_at.desc())))


def product_count(db: Session, business_id: uuid.UUID, *, include_archived: bool = False) -> int:
    from sqlalchemy import func

    stmt = select(func.count()).select_from(models.Product).where(
        models.Product.business_id == business_id
    )
    if not include_archived:
        stmt = stmt.where(models.Product.lifecycle_state != enums.ProductLifecycle.ARCHIVED.value)
    return int(db.scalar(stmt) or 0)


def _bump_version(
    db: Session,
    *,
    product: models.Product,
    business_id: uuid.UUID,
    changed_fields: dict,
    categories: list[str],
    risk: enums.ChangeRisk,
    trigger: enums.SyncTrigger,
    content_hash: str | None = None,
    mapping_version_id: uuid.UUID | None = None,
) -> models.ProductVersion:
    product.current_version += 1
    version = models.ProductVersion(
        product_id=product.product_id,
        business_id=business_id,
        version_no=product.current_version,
        change_categories=categories,
        risk_level=risk.value,
        changed_fields=changed_fields,
        content_hash=content_hash,
        mapping_version_id=mapping_version_id,
        trigger=trigger.value,
    )
    db.add(version)
    return version


def _apply_field(product: models.Product, field: str, value: object) -> None:
    if field in ("external_id", "sku", "barcode", "fingerprint"):
        setattr(product, field, value or None)
    elif field == "attributes":
        product.attributes = value if isinstance(value, dict) else {}
    else:
        setattr(product, field, value)


def update_product(
    db: Session,
    *,
    business_id: uuid.UUID,
    product_id: uuid.UUID,
    actor_id: uuid.UUID,
    correlation_id: str,
    fields: dict,
) -> tuple[models.Product, models.ProductVersion | None]:
    """Manual edit. Risk-classified; identity-field edits are CRITICAL and
    leave the previous identity in the version row (old ID retained)."""
    product = get_product(db, business_id, product_id)
    if product is None:
        raise LookupError("product not found")

    changes: dict = {}
    categories: list[str] = []
    risks: list[enums.ChangeRisk] = []
    for raw_field, raw_value in fields.items():
        if raw_field not in EDITABLE_FIELDS:
            continue
        old = getattr(product, raw_field)
        new = raw_value
        if raw_field in ("price", "stock"):
            new = int(new) if new not in (None, "") else None
        if raw_field == "attributes" and not isinstance(new, dict):
            continue
        if raw_field == "currency" and new:
            new = str(new).upper()[:8]
        old_norm = _canonical(old, raw_field)
        new_norm = _canonical(new, raw_field)
        if old_norm == new_norm:
            continue
        kind = enums.FieldKind.CORE if raw_field != "attributes" else enums.FieldKind.CUSTOM
        risk = change_risk.field_risk(raw_field, kind=kind, old_value=old, new_value=new)
        changes[raw_field] = {"old": _jsonable(old), "new": _jsonable(new)}
        if raw_field in change_risk.IDENTITY_FIELDS:
            categories.append(enums.ChangeCategory.IDENTITY_IDENTIFIER_CHANGED.value)
        elif raw_field == "attributes":
            categories.append(enums.ChangeCategory.CUSTOM_FIELD_CHANGED.value)
        else:
            categories.append(_field_category(raw_field))
        risks.append(risk)
        _apply_field(product, raw_field, new)
        if raw_field in change_risk.IDENTITY_FIELDS:
            # Old identity is retained (explainable, reversible).
            changes[raw_field]["retained_old"] = _jsonable(old)

    if not changes:
        return product, None

    identity_changed = enums.ChangeCategory.IDENTITY_IDENTIFIER_CHANGED.value in categories
    if identity_changed and product.lifecycle_state != enums.ProductLifecycle.REVIEW_REQUIRED.value:
        product.lifecycle_state = enums.ProductLifecycle.REVIEW_REQUIRED.value

    version = _bump_version(
        db,
        product=product,
        business_id=business_id,
        changed_fields=changes,
        categories=categories,
        risk=change_risk.max_risk(risks),
        trigger=enums.SyncTrigger.MANUAL,
    )
    AuditService(db).record(
        action="product.updated",
        actor_user_id=actor_id,
        business_id=business_id,
        target_type="product",
        target_id=str(product_id),
        meta={"version": version.version_no, "risk": version.risk_level, "fields": list(changes)},
    )
    db.flush()
    return product, version


def _field_category(field: str) -> str:
    """Core field -> ChangeCategory value (currency folds into price)."""
    return {
        "name": enums.ChangeCategory.NAME_CHANGED.value,
        "description": enums.ChangeCategory.DESCRIPTION_CHANGED.value,
        "price": enums.ChangeCategory.PRICE_CHANGED.value,
        "currency": enums.ChangeCategory.PRICE_CHANGED.value,
        "stock": enums.ChangeCategory.STOCK_CHANGED.value,
        "category": enums.ChangeCategory.CATEGORY_CHANGED.value,
    }.get(field, enums.ChangeCategory.SPECIFICATION_CHANGED.value)


def _canonical(value: object, field: str) -> object:
    if value in (None, ""):
        return None
    if field in ("price", "stock"):
        try:
            return int(value)
        except (TypeError, ValueError):
            return value
    if field in ("external_id", "sku", "barcode", "name", "category"):
        return normalize_text(str(value))
    return value


def _jsonable(value: object) -> object:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return str(value)


# --- Media -------------------------------------------------------------------


def add_media(
    db: Session,
    *,
    business_id: uuid.UUID,
    product_id: uuid.UUID,
    actor_id: uuid.UUID,
    correlation_id: str,
    url: str,
    origin: enums.MediaOrigin,
) -> models.ProductMedia:
    from sqlalchemy import func as _func

    product = get_product(db, business_id, product_id)
    if product is None:
        raise LookupError("product not found")
    position = int(
        db.scalar(
            select(_func.coalesce(_func.max(models.ProductMedia.position) + 1, 0)).where(
                models.ProductMedia.product_id == product_id
            )
        )
        or 0
    )
    media = models.ProductMedia(
        product_id=product_id,
        business_id=business_id,
        url=url,
        origin=origin.value,
        position=position,
        status=enums.MediaStatus.ACTIVE.value,
    )
    db.add(media)
    _bump_version(
        db,
        product=product,
        business_id=business_id,
        changed_fields={"media": {"added": url}},
        categories=[enums.ChangeCategory.MEDIA_CHANGED.value],
        risk=enums.ChangeRisk.LOW,
        trigger=enums.SyncTrigger.MANUAL,
    )
    AuditService(db).record(
        action="product.media.added",
        actor_user_id=actor_id,
        business_id=business_id,
        target_type="product",
        target_id=str(product_id),
        meta={"url": url, "origin": origin.value},
    )
    db.flush()
    return media


def remove_media(
    db: Session,
    *,
    business_id: uuid.UUID,
    product_id: uuid.UUID,
    media_id: uuid.UUID,
    actor_id: uuid.UUID,
    correlation_id: str,
) -> bool:
    media = db.scalar(
        select(models.ProductMedia).where(
            models.ProductMedia.media_id == media_id,
            models.ProductMedia.product_id == product_id,
            models.ProductMedia.business_id == business_id,
        )
    )
    if media is None:
        raise LookupError("media not found")
    media.status = enums.MediaStatus.REMOVED.value
    _bump_version(
        db,
        product=db.get(models.Product, product_id),
        business_id=business_id,
        changed_fields={"media": {"removed": media.url}},
        categories=[enums.ChangeCategory.MEDIA_CHANGED.value],
        risk=enums.ChangeRisk.LOW,
        trigger=enums.SyncTrigger.MANUAL,
    )
    AuditService(db).record(
        action="product.media.removed",
        actor_user_id=actor_id,
        business_id=business_id,
        target_type="product",
        target_id=str(product_id),
        meta={"url": media.url, "origin": media.origin},
    )
    db.flush()
    return True


def list_media(db: Session, product_id: uuid.UUID) -> list[models.ProductMedia]:
    return list(
        db.scalars(
            select(models.ProductMedia)
            .where(models.ProductMedia.product_id == product_id)
            .order_by(models.ProductMedia.position)
        )
    )


# --- Lifecycle ----------------------------------------------------------------


def archive_product(
    db: Session,
    *,
    business_id: uuid.UUID,
    product_id: uuid.UUID,
    actor_id: uuid.UUID,
    correlation_id: str,
) -> models.Product:
    product = get_product(db, business_id, product_id)
    if product is None:
        raise LookupError("product not found")
    if product.lifecycle_state == enums.ProductLifecycle.ARCHIVED.value:
        raise ValueError("already archived")
    product.lifecycle_state = enums.ProductLifecycle.ARCHIVED.value
    AuditService(db).record(
        action="product.archived",
        actor_user_id=actor_id,
        business_id=business_id,
        target_type="product",
        target_id=str(product_id),
        meta={},
    )
    db.flush()
    return product


def restore_product(
    db: Session,
    *,
    business_id: uuid.UUID,
    product_id: uuid.UUID,
    actor_id: uuid.UUID,
    correlation_id: str,
) -> models.Product:
    product = get_product(db, business_id, product_id)
    if product is None:
        raise LookupError("product not found")
    if product.lifecycle_state != enums.ProductLifecycle.ARCHIVED.value:
        raise ValueError("only archived products can be restored")
    product.lifecycle_state = enums.ProductLifecycle.ACTIVE.value
    AuditService(db).record(
        action="product.restored",
        actor_user_id=actor_id,
        business_id=business_id,
        target_type="product",
        target_id=str(product_id),
        meta={},
    )
    db.flush()
    return product


# --- Identity candidates (business-scoped) ------------------------------------


@dataclass(frozen=True)
class IdentityCandidateView:
    """Display data for review cases (the pure candidate carries only ids)."""

    product_id: uuid.UUID
    name: str
    external_id: str | None
    sku: str | None
    barcode: str | None
    category: str | None
    price: int | None
    lifecycle_state: str
    matched_by: tuple[str, ...]


def find_identity_candidates(
    db: Session,
    business_id: uuid.UUID,
    *,
    external_id: str | None,
    sku: str | None,
    barcode: str | None,
    fingerprint: str | None,
    name: str | None,
) -> tuple[list[IdentityCandidate], list[IdentityCandidateView]]:
    """In-business products matching at least one identity field.

    Returns (pure candidates for ``resolve_identity``, display views).
    Matching uses normalized values; archived products are excluded.
    """
    conditions = []
    n_ext = normalize_text(external_id)
    n_sku = normalize_text(sku)
    n_bar = normalize_text(barcode)
    n_name = normalize_text(name)

    def _either(column, raw: str | None, norm: str | None):
        """Match stored raw OR normalized form (stored values are raw source
        text; normalization keeps cross-run case/variant drift matching)."""
        values = {v for v in (raw, norm) if v}
        return column.in_(list(values))

    if external_id or n_ext:
        conditions.append(_either(models.Product.external_id, external_id, n_ext))
    if sku or n_sku:
        conditions.append(_either(models.Product.sku, sku, n_sku))
    if barcode or n_bar:
        conditions.append(_either(models.Product.barcode, barcode, n_bar))
    if fingerprint:
        conditions.append(models.Product.fingerprint == fingerprint)
    if n_name:
        # Name is always a candidate-discovery input; the pure resolver
        # guarantees it can never auto-match (matched_by="name" evidence).
        conditions.append(_either(models.Product.name, name, n_name))
    if not conditions:
        return [], []
    rows = db.scalars(
        select(models.Product)
        .where(
            models.Product.business_id == business_id,
            models.Product.lifecycle_state != enums.ProductLifecycle.ARCHIVED.value,
            or_(*conditions),
        )
    )
    out: list[IdentityCandidate] = []
    views: list[IdentityCandidateView] = []
    for p in rows:
        evidence: list[str] = []
        if n_ext and p.external_id and normalize_text(p.external_id) == n_ext:
            evidence.append("external_id")
        if n_sku and p.sku and normalize_text(p.sku) == n_sku:
            evidence.append("sku")
        if n_bar and p.barcode and normalize_text(p.barcode) == n_bar:
            evidence.append("barcode")
        if fingerprint and p.fingerprint == fingerprint:
            evidence.append("fingerprint")
        if n_name and p.name and normalize_text(p.name) == n_name:
            evidence.append("name")
        if not evidence:
            continue
        out.append(
            IdentityCandidate(
                product_id=str(p.product_id),
                external_id=p.external_id,
                sku=p.sku,
                barcode=p.barcode,
                fingerprint=p.fingerprint,
                name_for_match=p.name,
            )
        )
        views.append(
            IdentityCandidateView(
                product_id=p.product_id,
                name=p.name,
                external_id=p.external_id,
                sku=p.sku,
                barcode=p.barcode,
                category=p.category,
                price=p.price,
                lifecycle_state=p.lifecycle_state,
                matched_by=tuple(evidence),
            )
        )
    return out, views
