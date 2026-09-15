"""Import / sync pipeline (manual trigger in V1).

Flow (spec SOURCE_SYNC sections 13-16, 21-22):
    entitlement gate -> structural read -> per-row extraction + validation
    -> identity resolution (pure domain) -> upsert / create / review case
    -> missing-row inference (only on confirmed complete reads)
    -> ImportRun summary + audit

Safety invariants:
- incomplete reads never trigger missing inference;
- ambiguous / conflicting identity NEVER auto-merges (review case);
- identity-field changes are always CRITICAL with a review case;
- mass HIGH-risk changes quarantine the remaining rows of the run;
- new products respect the products entitlement limit;
- products in REVIEW_REQUIRED are frozen for automatic updates.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application.audit import AuditService, get_correlation_id
from app.domain import change_risk, enums
from app.domain.identity import product_fingerprint, resolve_identity
from app.infrastructure.db import models
from app.infrastructure.sources import read_source

from . import products as products_svc
from .mapping import active_mapping_for

# Core fields the pipeline writes onto Product (media handled separately).
_CORE_WRITE_FIELDS = (
    "name",
    "description",
    "price",
    "currency",
    "stock",
    "category",
    "external_id",
    "sku",
    "barcode",
)


@dataclass
class RowResult:
    locator: str
    outcome: str
    product_id: str | None = None
    error: str | None = None
    risk: str | None = None


@dataclass
class ImportOutcome:
    run: models.ImportRun
    row_results: list[RowResult] = field(default_factory=list)
    review_cases: list[models.ReviewCase] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        return self.run.counts


def _new_counts() -> dict[str, int]:
    return {
        "read": 0,
        "valid": 0,
        "new": 0,
        "changed": 0,
        "unchanged": 0,
        "missing": 0,
        "reappeared": 0,
        "ambiguous": 0,
        "conflict": 0,
        "duplicate": 0,
        "blocked": 0,
        "invalid": 0,
        "blank": 0,
    }


# Row-result outcome literals (action outcomes) — quality outcomes reuse the
# RowOutcome enum values.
_OUTCOME_NEW = "NEW"
_OUTCOME_CHANGED = "CHANGED"


# --- extraction & validation ---------------------------------------------------


def _parse_int(value: str) -> int | None:
    v = value.strip().replace(",", "").replace("٬", "")
    if not v:
        return None
    try:
        return int(v)
    except ValueError:
        try:
            return int(float(v))
        except ValueError:
            return None


def extract_row(
    row: dict[str, str], mapping: models.SourceMapping
) -> tuple[dict, dict, list[str]]:
    """Map raw row -> (core values, attributes, validation errors)."""
    core: dict = {}
    attrs: dict = {}
    errors: list[str] = []
    for entry in mapping.entries:
        column = entry.get("column", "")
        raw = (row.get(column) or "").strip()
        kind = entry.get("field_kind")
        if kind == enums.FieldKind.CORE.value:
            cf = entry.get("canonical_field")
            if not cf:
                continue
            if cf in ("image1", "image2", "image3"):
                if raw and not raw.startswith(("http://", "https://")):
                    errors.append(f"column '{column}': invalid media URL")
                core[cf] = raw or None
                continue
            if cf in ("price", "stock"):
                parsed = _parse_int(raw)
                if raw and parsed is None:
                    errors.append(f"column '{column}': not a number")
                core[cf] = parsed
            elif cf == "currency":
                core[cf] = raw.upper()[:8] or None
            else:
                core[cf] = raw or None
        elif kind == enums.FieldKind.CUSTOM.value:
            display = entry.get("display_name") or column
            if raw:
                attrs[display] = raw
    # Required validation comes from the active mapping, not a hard-coded
    # field list. This keeps the pipeline extensible for business-specific
    # required columns while preserving the default name requirement.
    for entry in mapping.entries:
        if not entry.get("required"):
            continue
        canonical = entry.get("canonical_field") or entry.get("column")
        if entry.get("field_kind") == enums.FieldKind.CORE.value:
            value = core.get(canonical)
        else:
            value = attrs.get(entry.get("display_name") or entry.get("column"))
        if value is None or (isinstance(value, str) and not value.strip()):
            column = entry.get("column", canonical)
            if canonical == "name":
                # Preserve the established API/test wording while adding the
                # exact source column for customer correction.
                errors.append(f"name is required but missing (column '{column}')")
            else:
                errors.append(f"column '{column}': required value is missing")
    return core, attrs, errors


def _invalid_outcome(errors: list[str]) -> str:
    if any("required" in e for e in errors):
        return enums.RowOutcome.INVALID_REQUIRED_FIELD.value
    return enums.RowOutcome.INVALID_TYPE.value


def _fingerprint_for(core: dict, attrs: dict) -> str:
    """Approved composition: name + category + first technical spec
    (first custom attribute), falling back to name + description[:120]."""
    spec_value = next(iter(attrs.values()), None) if attrs else None
    return product_fingerprint(
        name=core.get("name"),
        category=core.get("category"),
        spec_value=spec_value,
        description=core.get("description"),
    )


# --- identity -------------------------------------------------------------------


def _resolve_row_identity(
    db: Session,
    *,
    business_id: uuid.UUID,
    core: dict,
) -> tuple[str, models.Product | None, list[products_svc.IdentityCandidateView]]:
    """Resolve one row. Returns (action, product, candidate_views).

    action in: MATCHED | NEW | AMBIGUOUS | CONFLICT | DUPLICATE
    Cross-field conflict (external_id says product A, sku says product B)
    is detected here BEFORE the priority resolution — that combination is
    exactly the identity-conflict case and never auto-resolves.
    """
    from app.domain.identity import normalize_text

    n_ext = normalize_text(core.get("external_id"))
    n_sku = normalize_text(core.get("sku"))
    n_bar = normalize_text(core.get("barcode"))
    fp = core.get("_fingerprint")

    candidates, views = products_svc.find_identity_candidates(
        db,
        business_id,
        external_id=core.get("external_id"),
        sku=core.get("sku"),
        barcode=core.get("barcode"),
        fingerprint=fp or None,
        name=core.get("name"),
    )
    if not candidates:
        return "NEW", None, []

    # Cross-field conflict: different products hold the row's identifiers.
    if n_ext:
        ext_holders = {v.product_id for v in views if "external_id" in v.matched_by}
        if n_sku:
            sku_holders = {v.product_id for v in views if "sku" in v.matched_by}
            if len(ext_holders) == 1 and len(sku_holders) == 1 and ext_holders != sku_holders:
                return "CONFLICT", None, views
        if n_bar:
            bar_holders = {v.product_id for v in views if "barcode" in v.matched_by}
            if len(ext_holders) == 1 and len(bar_holders) == 1 and ext_holders != bar_holders:
                return "CONFLICT", None, views
    elif n_sku and n_bar:
        sku_holders = {v.product_id for v in views if "sku" in v.matched_by}
        bar_holders = {v.product_id for v in views if "barcode" in v.matched_by}
        if len(sku_holders) == 1 and len(bar_holders) == 1 and sku_holders != bar_holders:
            return "CONFLICT", None, views
    # (Multiple products sharing one identifier value surface as AMBIGUOUS
    # inside the pure resolver — review case, never auto-merge.)

    evidence = resolve_identity(
        external_id=core.get("external_id"),
        sku=core.get("sku"),
        barcode=core.get("barcode"),
        fingerprint=fp or None,
        name=core.get("name"),
        candidates=candidates,
    )
    if evidence.outcome in (
        enums.IdentityOutcome.EXACT_MATCH,
        enums.IdentityOutcome.CONFIDENT_MATCH,
    ):
        if evidence.matched_product_id is None:
            return "NEW", None, views
        product = db.get(models.Product, uuid.UUID(evidence.matched_product_id))
        if product is None or product.business_id != business_id:
            return "NEW", None, views
        return "MATCHED", product, views
    if evidence.outcome is enums.IdentityOutcome.AMBIGUOUS:
        return "AMBIGUOUS", None, views
    if evidence.outcome is enums.IdentityOutcome.IDENTITY_CONFLICT:
        return "CONFLICT", None, views
    if evidence.outcome is enums.IdentityOutcome.POSSIBLE_DUPLICATE:
        return "DUPLICATE", None, views
    return "NEW", None, views


def _views_payload(views: list[products_svc.IdentityCandidateView]) -> list[dict]:
    return [
        {
            "product_id": str(v.product_id),
            "name": v.name,
            "sku": v.sku,
            "external_id": v.external_id,
            "barcode": v.barcode,
            "price": v.price,
            "lifecycle_state": v.lifecycle_state,
            "matched_by": list(v.matched_by),
        }
        for v in views
    ]


def _check_duplicate_external_id(
    db: Session,
    business_id: uuid.UUID,
    core: dict,
    matched_product: models.Product | None,
) -> bool:
    """A row's external_id already belongs to a DIFFERENT product."""
    ext = core.get("external_id")
    if not ext:
        return False
    exclude = matched_product.product_id if matched_product else uuid.UUID(int=0)
    other = db.scalar(
        select(models.Product).where(
            models.Product.business_id == business_id,
            models.Product.external_id == ext,
            models.Product.product_id != exclude,
        )
    )
    return other is not None


# --- upsert ---------------------------------------------------------------------


def _media_reconcile(
    db: Session,
    *,
    product: models.Product,
    source: models.Source,
    core: dict,
    business_id: uuid.UUID,
) -> list[str]:
    """Reconcile SOURCE-origin media for the mapped image slots.

    Default (media_authoritative=False): add new source URLs, keep existing
    source media, NEVER touch CUSTOMER media.
    media_authoritative=True: source URLs not present in this read are soft-
    removed (REMOVED), still non-destructive (restorable).
    """
    new_urls = [core.get(k) for k in ("image1", "image2", "image3") if core.get(k)]
    if not new_urls:
        return []
    existing = list(
        db.scalars(
            select(models.ProductMedia).where(models.ProductMedia.product_id == product.product_id)
        )
    )
    existing_urls = {m.url for m in existing}
    changed: list[str] = []
    pos = max((m.position for m in existing), default=-1)
    for url in new_urls:
        if url not in existing_urls:
            pos += 1
            db.add(
                models.ProductMedia(
                    product_id=product.product_id,
                    business_id=business_id,
                    media_type="IMAGE",
                    origin=enums.MediaOrigin.SOURCE.value,
                    url=url,
                    position=pos,
                    status=enums.MediaStatus.ACTIVE.value,
                )
            )
            existing_urls.add(url)
            changed.append(url)
    if source.media_authoritative:
        for m in existing:
            if (
                m.origin == enums.MediaOrigin.SOURCE.value
                and m.status == enums.MediaStatus.ACTIVE.value
                and m.url not in new_urls
            ):
                m.status = enums.MediaStatus.REMOVED.value
                changed.append(f"removed:{m.url}")
    return changed


def _upsert_product(
    db: Session,
    *,
    business_id: uuid.UUID,
    product: models.Product | None,
    core: dict,
    attrs: dict,
    source: models.Source,
    mapping: models.SourceMapping,
    content_hash: str,
) -> tuple[models.Product, models.ProductVersion | None, list[str]]:
    """Apply a validated row to an existing product or create a new one.

    Returns (product, version|None, categories).
    """
    fp = core.get("_fingerprint") or ""
    if product is None:
        product = models.Product(
            business_id=business_id,
            name=core.get("name"),
            category=core.get("category"),
            description=core.get("description"),
            price=core.get("price"),
            currency=(core.get("currency") or "IRT").upper()[:8],
            stock=core.get("stock"),
            external_id=core.get("external_id") or None,
            sku=core.get("sku") or None,
            barcode=core.get("barcode") or None,
            fingerprint=fp or None,
            lifecycle_state=enums.ProductLifecycle.ACTIVE.value,
            current_version=1,
            attributes=attrs,
        )
        db.add(product)
        db.flush()
        db.add(
            models.ProductVersion(
                product_id=product.product_id,
                business_id=business_id,
                version_no=1,
                change_categories=["PRODUCT_CREATED"],
                risk_level=enums.ChangeRisk.LOW.value,
                changed_fields={"created": True},
                content_hash=content_hash,
                mapping_version_id=mapping.mapping_id,
                trigger=enums.SyncTrigger.MANUAL.value,
            )
        )
        _media_reconcile(
            db, product=product, source=source, core=core, business_id=business_id
        )
        return product, None, ["PRODUCT_CREATED"]

    # Reappearance or correction reconnects automatically. The source-invalid
    # state is deliberately reversible: the corrected row is the evidence.
    if product.lifecycle_state in (
        enums.ProductLifecycle.MISSING_FROM_SOURCE.value,
        enums.ProductLifecycle.SOURCE_INVALID.value,
    ):
        product.lifecycle_state = enums.ProductLifecycle.ACTIVE.value

    changed_fields: dict = {}
    categories: list[str] = []
    risks: list[enums.ChangeRisk] = []
    for cf in _CORE_WRITE_FIELDS:
        # Unmapped columns never touch the product (absent from ``core``).
        if cf not in core:
            continue
        new_v = core.get(cf)
        # Empty source values never clear identity fields or currency in V1
        # (clearing identity would be a CRITICAL identity change by accident).
        if new_v is None and cf in (*change_risk.IDENTITY_FIELDS, "currency"):
            continue
        if cf in ("price", "stock"):
            old_v = product.price if cf == "price" else product.stock
            new_v = int(new_v) if new_v is not None else None
        else:
            old_v = getattr(product, cf)
        old_norm = products_svc._canonical(old_v, cf)
        new_norm = products_svc._canonical(new_v, cf)
        if old_norm == new_norm:
            continue
        risk = change_risk.field_risk(
            cf, kind=enums.FieldKind.CORE, old_value=old_v, new_value=new_v
        )
        changed_fields[cf] = {
            "old": products_svc._jsonable(old_v),
            "new": products_svc._jsonable(new_v),
        }
        if cf in change_risk.IDENTITY_FIELDS:
            categories.append(enums.ChangeCategory.IDENTITY_IDENTIFIER_CHANGED.value)
            changed_fields[cf]["retained_old"] = products_svc._jsonable(old_v)
        else:
            categories.append(products_svc._field_category(cf))
        risks.append(risk)
        setattr(product, cf, new_v if new_v is not None else None)

    if (fp or None) != product.fingerprint:
        product.fingerprint = fp or None

    # Attributes merge (custom fields).
    new_attrs = dict(product.attributes or {})
    new_attrs.update(attrs)
    if new_attrs != (product.attributes or {}):
        changed_fields["attributes"] = {"new": new_attrs}
        categories.append(enums.ChangeCategory.CUSTOM_FIELD_CHANGED.value)
        risks.append(change_risk.CUSTOM_FIELD_DEFAULT_RISK)
        product.attributes = new_attrs

    media_changes = _media_reconcile(
        db, product=product, source=source, core=core, business_id=business_id
    )
    if media_changes:
        changed_fields["media"] = {"changes": media_changes}
        categories.append(enums.ChangeCategory.MEDIA_CHANGED.value)

    if not changed_fields:
        return product, None, []

    identity_changed = enums.ChangeCategory.IDENTITY_IDENTIFIER_CHANGED.value in categories
    if identity_changed:
        product.lifecycle_state = enums.ProductLifecycle.REVIEW_REQUIRED.value
        risks.append(enums.ChangeRisk.CRITICAL)

    product.current_version += 1
    version = models.ProductVersion(
        product_id=product.product_id,
        business_id=business_id,
        version_no=product.current_version,
        change_categories=categories,
        risk_level=change_risk.max_risk(risks).value,
        changed_fields=changed_fields,
        content_hash=content_hash,
        mapping_version_id=mapping.mapping_id,
        trigger=enums.SyncTrigger.MANUAL.value,
    )
    db.add(version)
    return product, version, categories


def _upsert_source_record(
    db: Session,
    *,
    source: models.Source,
    business_id: uuid.UUID,
    locator: str,
    core: dict,
    content_hash: str,
    product: models.Product | None,
    mapping: models.SourceMapping,
) -> models.SourceRecord:
    record = db.scalar(
        select(models.SourceRecord).where(
            models.SourceRecord.source_id == source.source_id,
            models.SourceRecord.locator == locator,
        )
    )
    now = datetime.now(UTC)
    if record is None:
        record = models.SourceRecord(
            source_id=source.source_id,
            business_id=business_id,
            locator=locator,
            state="PRESENT",
            last_seen_at=now,
        )
        db.add(record)
    record.external_key = core.get("external_id") or None
    record.sku = core.get("sku") or None
    record.barcode = core.get("barcode") or None
    record.fingerprint = core.get("_fingerprint") or None
    record.content_hash = content_hash
    record.product_id = product.product_id if product else None
    record.mapping_version_id = mapping.mapping_id
    record.state = "PRESENT"
    record.last_seen_at = now
    return record


def _content_hash_for(core: dict, attrs: dict, mapping: models.SourceMapping) -> str:
    from app.domain.identity import text_fingerprint

    parts = []
    for entry in mapping.entries:
        if entry.get("field_kind") == enums.FieldKind.CORE.value:
            parts.append(str(core.get(entry.get("canonical_field"), "") or ""))
        else:
            parts.append(
                str(attrs.get(entry.get("display_name") or entry.get("column"), "") or "")
            )
    return text_fingerprint(*parts)


# --- helpers ---------------------------------------------------------------------


def _entitlement_products_limit(db: Session, business_id: uuid.UUID) -> int | None:
    from app.application.entitlements import get_entitlements

    ent = get_entitlements(db, business_id=business_id)
    return ent.product_limit


def _make_run(
    db: Session,
    *,
    source: models.Source,
    business_id: uuid.UUID,
    mapping: models.SourceMapping | None,
    actor_id: uuid.UUID | None,
    correlation_id: str,
    status: enums.ImportRunStatus,
    counts: dict,
    row_errors: list,
    failure_summary: str | None = None,
) -> models.ImportRun:
    run = models.ImportRun(
        source_id=source.source_id,
        business_id=business_id,
        mapping_version_id=mapping.mapping_id if mapping else None,
        trigger=enums.SyncTrigger.MANUAL.value,
        status=status.value,
        requested_by=actor_id,
        counts=counts,
        row_errors=row_errors,
        failure_summary=failure_summary,
        correlation_id=correlation_id or get_correlation_id(),
    )
    run.finished_at = datetime.now(UTC)
    db.add(run)
    db.flush()
    return run


def _create_review_case(
    db: Session,
    *,
    business_id: uuid.UUID,
    kind: enums.ReviewCaseKind,
    payload: dict,
    product_id: uuid.UUID | None = None,
    source_id: uuid.UUID | None = None,
) -> models.ReviewCase:
    case = models.ReviewCase(
        business_id=business_id,
        kind=kind.value,
        product_id=product_id,
        source_id=source_id,
        payload=payload,
        status=enums.ReviewCaseStatus.OPEN.value,
    )
    db.add(case)
    db.flush()
    return case


# Module-level reappearance counter (per source); incremented in run_import
# when a row re-matches a MISSING_FROM_SOURCE product.
_reappeared_counter: dict[uuid.UUID, int] = {}


# --- main entrypoints -------------------------------------------------------------


def run_import(
    db: Session,
    *,
    source: models.Source,
    business_id: uuid.UUID,
    actor_id: uuid.UUID,
    correlation_id: str,
    file_bytes: bytes | None = None,
) -> ImportOutcome:
    """Execute a full import run (writes). Raises ValueError on read failure."""
    mapping = active_mapping_for(db, source)
    if mapping is None:
        raise ValueError("source has no active mapping")

    read = read_source(source, file_bytes=file_bytes)
    counts = _new_counts()
    row_errors: list[dict] = []
    row_results: list[RowResult] = []
    if not read.complete or read.error:
        run = _make_run(
            db,
            source=source,
            business_id=business_id,
            mapping=mapping,
            actor_id=actor_id,
            correlation_id=correlation_id,
            status=enums.ImportRunStatus.FAILED,
            counts=counts,
            row_errors=row_errors,
            failure_summary=read.error,
        )
        AuditService(db).record(
            action="import.run.failed",
            actor_user_id=actor_id,
            business_id=business_id,
            target_type="import_run",
            target_id=str(run.run_id),
            meta={"reason": read.error},
        )
        db.flush()
        return ImportOutcome(run=run, row_results=[])

    counts["read"] = read.row_count
    # Snapshot of products linked to this source BEFORE this run (used by
    # missing-row inference after rows are processed).
    prior_product_ids = {
        r.product_id
        for r in db.scalars(
            select(models.SourceRecord).where(
                models.SourceRecord.source_id == source.source_id,
                models.SourceRecord.business_id == business_id,
                models.SourceRecord.product_id.is_not(None),
            )
        )
        if r.product_id is not None
    }
    product_limit = _entitlement_products_limit(db, business_id)
    current_products = products_svc.product_count(db, business_id)
    new_created = 0
    high_risk_by_field: dict[str, set[uuid.UUID]] = {}
    quarantined_field: str | None = None
    review_cases: list[models.ReviewCase] = []
    seen_locators: set[str] = set()

    for idx, row in enumerate(read.rows):
        locator = f"row:{idx + 2}"  # header is row 1
        seen_locators.add(locator)

        # Spreadsheet readers commonly preserve empty trailing rows. They are
        # not invalid products and must not create review noise or affect
        # missing-row inference.
        if not any(str(value or "").strip() for value in row.values()):
            counts["blank"] += 1
            row_results.append(RowResult(locator=locator, outcome="BLANK"))
            continue

        core, attrs, errors = extract_row(row, mapping)
        if errors:
            counts["invalid"] += 1
            row_errors.append({"locator": locator, "errors": errors})
            # If identity data is still sufficient to locate an existing
            # product, deactivate it until the customer fixes this exact row.
            partial_core = dict(core)
            if (
                partial_core.get("external_id")
                or partial_core.get("sku")
                or partial_core.get("barcode")
            ):
                partial_core["_fingerprint"] = None
                action, invalid_product, _ = _resolve_row_identity(
                    db, business_id=business_id, core=partial_core
                )
                if action == "MATCHED" and invalid_product is not None:
                    invalid_product.lifecycle_state = enums.ProductLifecycle.SOURCE_INVALID.value
            row_results.append(
                RowResult(
                    locator=locator,
                    outcome=_invalid_outcome(errors),
                    error="; ".join(errors),
                )
            )
            continue
        counts["valid"] += 1
        core["_fingerprint"] = _fingerprint_for(core, attrs)
        content_hash = _content_hash_for(core, attrs, mapping)

        outcome, product, views = _resolve_row_identity(db, business_id=business_id, core=core)

        if quarantined_field is not None:
            counts["blocked"] += 1
            row_errors.append(
                {
                    "locator": locator,
                    "errors": [f"quarantined: mass HIGH-risk changes on '{quarantined_field}'"],
                }
            )
            row_results.append(
                RowResult(
                    locator=locator,
                    outcome=enums.RowOutcome.BLOCKED.value,
                    error=f"quarantine:{quarantined_field}",
                )
            )
            continue

        if outcome == "AMBIGUOUS":
            counts["ambiguous"] += 1
            review_cases.append(
                _create_review_case(
                    db,
                    business_id=business_id,
                    kind=enums.ReviewCaseKind.IDENTITY_AMBIGUOUS,
                    source_id=source.source_id,
                    payload={
                        "locator": locator,
                        "row": dict(row),
                        "candidates": _views_payload(views),
                    },
                )
            )
            row_results.append(
                RowResult(locator=locator, outcome=enums.RowOutcome.IDENTITY_AMBIGUOUS.value)
            )
            continue

        if outcome == "CONFLICT":
            counts["conflict"] += 1
            review_cases.append(
                _create_review_case(
                    db,
                    business_id=business_id,
                    kind=enums.ReviewCaseKind.IDENTITY_CONFLICT,
                    source_id=source.source_id,
                    payload={
                        "locator": locator,
                        "row": dict(row),
                        "external_id": core.get("external_id"),
                        "sku": core.get("sku"),
                        "barcode": core.get("barcode"),
                        "candidates": _views_payload(views),
                    },
                )
            )
            row_results.append(
                RowResult(locator=locator, outcome=enums.RowOutcome.IDENTITY_CONFLICT.value)
            )
            continue

        if outcome == "DUPLICATE":
            counts["duplicate"] += 1
            review_cases.append(
                _create_review_case(
                    db,
                    business_id=business_id,
                    kind=enums.ReviewCaseKind.DUPLICATE_CANDIDATE,
                    source_id=source.source_id,
                    payload={
                        "locator": locator,
                        "row": dict(row),
                        "candidates": _views_payload(views),
                    },
                )
            )
            row_results.append(
                RowResult(locator=locator, outcome=enums.RowOutcome.DUPLICATE_CANDIDATE.value)
            )
            continue

        if _check_duplicate_external_id(db, business_id, core, product):
            counts["conflict"] += 1
            review_cases.append(
                _create_review_case(
                    db,
                    business_id=business_id,
                    kind=enums.ReviewCaseKind.IDENTITY_CONFLICT,
                    source_id=source.source_id,
                    payload={
                        "locator": locator,
                        "row": dict(row),
                        "reason": "external_id belongs to a different product",
                        "external_id": core.get("external_id"),
                        "matched_product": str(product.product_id) if product else None,
                    },
                )
            )
            row_results.append(
                RowResult(locator=locator, outcome=enums.RowOutcome.IDENTITY_CONFLICT.value)
            )
            continue

        # Frozen product: automatic updates are suspended while under review.
        if (
            product is not None
            and product.lifecycle_state == enums.ProductLifecycle.REVIEW_REQUIRED.value
        ):
            counts["blocked"] += 1
            row_errors.append(
                {
                    "locator": locator,
                    "errors": [
                        "product under review (REVIEW_REQUIRED) - automatic updates frozen"
                    ],
                }
            )
            row_results.append(
                RowResult(
                    locator=locator,
                    outcome=enums.RowOutcome.BLOCKED.value,
                    product_id=str(product.product_id),
                    error="product_under_review",
                )
            )
            # Still record presence so missing-inference does not flag it.
            _upsert_source_record(
                db,
                source=source,
                business_id=business_id,
                locator=locator,
                core=core,
                content_hash=content_hash,
                product=product,
                mapping=mapping,
            )
            continue

        if outcome == "NEW":
            if product_limit is not None and (current_products + new_created) >= product_limit:
                counts["blocked"] += 1
                row_errors.append(
                    {"locator": locator, "errors": ["products entitlement limit reached"]}
                )
                row_results.append(
                    RowResult(
                        locator=locator,
                        outcome=enums.RowOutcome.BLOCKED.value,
                        error="entitlement_exceeded",
                    )
                )
                continue
            product, _version, _cats = _upsert_product(
                db,
                business_id=business_id,
                product=None,
                core=core,
                attrs=attrs,
                source=source,
                mapping=mapping,
                content_hash=content_hash,
            )
            new_created += 1
            counts["new"] += 1
            row_results.append(
                RowResult(locator=locator, outcome=_OUTCOME_NEW, product_id=str(product.product_id))
            )
        else:  # MATCHED
            reappearing = (
                product is not None
                and product.lifecycle_state == enums.ProductLifecycle.MISSING_FROM_SOURCE.value
            )
            product, version, _cats = _upsert_product(
                db,
                business_id=business_id,
                product=product,
                core=core,
                attrs=attrs,
                source=source,
                mapping=mapping,
                content_hash=content_hash,
            )
            if reappearing:
                _reappeared_counter[source.source_id] = (
                    _reappeared_counter.get(source.source_id, 0) + 1
                )
            if version is not None:
                counts["changed"] += 1
                row_results.append(
                    RowResult(
                        locator=locator,
                        outcome=_OUTCOME_CHANGED,
                        product_id=str(product.product_id),
                        risk=version.risk_level,
                    )
                )
                # Track mass HIGH-risk changes per field.
                if version.risk_level in (enums.ChangeRisk.HIGH, enums.ChangeRisk.CRITICAL):
                    for cf in version.changed_fields:
                        if cf in ("attributes", "media", "created"):
                            continue
                        if cf in change_risk.CORE_FIELD_RISK or cf in change_risk.IDENTITY_FIELDS:
                            high_risk_by_field.setdefault(cf, set()).add(product.product_id)
                    if "attributes" in version.changed_fields:
                        high_risk_by_field.setdefault("attributes", set()).add(
                            product.product_id
                        )
                    for cf, pids in high_risk_by_field.items():
                        if len(pids) >= change_risk.SUSPICIOUS_MASS_CHANGE_COUNT:
                            quarantined_field = cf
                            review_cases.append(
                                _create_review_case(
                                    db,
                                    business_id=business_id,
                                    kind=enums.ReviewCaseKind.SUSPICIOUS_CHANGE,
                                    source_id=source.source_id,
                                    payload={
                                        "field": cf,
                                        "products": [str(p) for p in pids],
                                        "note": (
                                            "changes already applied before "
                                            "detection remain applied"
                                        ),
                                    },
                                )
                            )
                            break
            else:
                counts["unchanged"] += 1
                row_results.append(
                    RowResult(
                        locator=locator,
                        outcome=enums.RowOutcome.UNCHANGED.value,
                        product_id=str(product.product_id),
                    )
                )

        _upsert_source_record(
            db,
            source=source,
            business_id=business_id,
            locator=locator,
            core=core,
            content_hash=content_hash,
            product=product,
            mapping=mapping,
        )

    db.flush()

    # --- Missing-row inference (only on confirmed complete reads) -------------
    missing_count = 0
    mass_missing_blocked = False
    if read.complete:
        seen_records = db.scalars(
            select(models.SourceRecord).where(
                models.SourceRecord.source_id == source.source_id,
                models.SourceRecord.business_id == business_id,
                models.SourceRecord.locator.in_(list(seen_locators)),
            )
        )
        seen_product_ids = {
            r.product_id for r in seen_records if r.product_id is not None
        }
        # Stale records: locators not present in this read. Records of
        # products that re-appeared elsewhere (moved rows) are dropped;
        # the rest are dropped too — the product lifecycle state carries
        # the missing flag, the record table always mirrors the last
        # confirmed read.
        stale = db.scalars(
            select(models.SourceRecord).where(
                models.SourceRecord.source_id == source.source_id,
                models.SourceRecord.business_id == business_id,
                ~models.SourceRecord.locator.in_(list(seen_locators)),
            )
        )
        for r in stale:
            db.delete(r)
        missing_candidates = prior_product_ids - seen_product_ids
        baseline = source.last_baseline_count
        if not baseline:
            baseline = products_svc.product_count(db, business_id, include_archived=False) or 1
        if missing_candidates and len(missing_candidates) > int(
            change_risk.MASS_MISSING_RATIO * baseline
        ):
            mass_missing_blocked = True
            review_cases.append(
                _create_review_case(
                    db,
                    business_id=business_id,
                    kind=enums.ReviewCaseKind.MASS_MISSING_BLOCKED,
                    source_id=source.source_id,
                    payload={
                        "unseen": len(missing_candidates),
                        "baseline": baseline,
                        "note": "no missing inference applied \u2014 confirm the read",
                    },
                )
            )
            row_errors.append(
                {
                    "locator": "*",
                    "errors": [
                        "MASS_MISSING_BLOCKED: unseen rows exceed 50% of baseline - "
                        "no missing inference applied"
                    ],
                }
            )
        else:
            for pid in missing_candidates:
                product = db.get(models.Product, pid)
                if product is None or product.business_id != business_id:
                    continue
                if product.lifecycle_state == enums.ProductLifecycle.ACTIVE.value:
                    product.lifecycle_state = enums.ProductLifecycle.MISSING_FROM_SOURCE.value
                    missing_count += 1
        counts["missing"] = missing_count
    counts["reappeared"] = _reappeared_counter.pop(source.source_id, 0)

    # --- Finalize source + run -------------------------------------------------
    source.last_sync_at = datetime.now(UTC)
    source.last_baseline_count = products_svc.product_count(
        db, business_id, include_archived=False
    )
    if source.status == enums.SourceStatus.PENDING_MAPPING.value:
        source.status = enums.SourceStatus.ACTIVE.value

    has_issues = (
        counts["invalid"]
        + counts["ambiguous"]
        + counts["conflict"]
        + counts["duplicate"]
        + counts["blocked"]
    ) > 0
    run = _make_run(
        db,
        source=source,
        business_id=business_id,
        mapping=mapping,
        actor_id=actor_id,
        correlation_id=correlation_id,
        status=(
            enums.ImportRunStatus.SUCCEEDED_WITH_ERRORS
            if has_issues
            else enums.ImportRunStatus.SUCCEEDED
        ),
        counts=counts,
        row_errors=row_errors,
    )
    AuditService(db).record(
        action="import.run.completed",
        actor_user_id=actor_id,
        business_id=business_id,
        target_type="import_run",
        target_id=str(run.run_id),
        meta={
            "counts": counts,
            "review_cases": [str(c.case_id) for c in review_cases],
            "mass_missing_blocked": mass_missing_blocked,
        },
    )
    db.flush()
    return ImportOutcome(run=run, row_results=row_results, review_cases=review_cases)


def preview_import(
    db: Session,
    *,
    source: models.Source,
    business_id: uuid.UUID,
    file_bytes: bytes | None = None,
) -> dict:
    """Dry run: same extraction/validation/identity logic, zero writes."""
    mapping = active_mapping_for(db, source)
    if mapping is None:
        return {
            "error": "source has no active mapping",
            "complete": False,
            "headers": [],
            "rows": [],
        }
    read = read_source(source, file_bytes=file_bytes)
    if not read.complete or read.error:
        return {"error": read.error, "complete": False, "headers": read.headers, "rows": []}

    counts = _new_counts()
    rows_out: list[dict] = []
    product_limit = _entitlement_products_limit(db, business_id)
    current_products = products_svc.product_count(db, business_id)
    new_predicted = 0
    for idx, row in enumerate(read.rows):
        locator = f"row:{idx + 2}"
        if not any(str(value or "").strip() for value in row.values()):
            counts["blank"] += 1
            rows_out.append({"locator": locator, "outcome": "BLANK"})
            continue
        core, attrs, errors = extract_row(row, mapping)
        if errors:
            counts["invalid"] += 1
            rows_out.append(
                {"locator": locator, "outcome": _invalid_outcome(errors), "errors": errors}
            )
            continue
        counts["valid"] += 1
        core["_fingerprint"] = _fingerprint_for(core, attrs)
        outcome, product, views = _resolve_row_identity(db, business_id=business_id, core=core)
        if outcome == "AMBIGUOUS":
            counts["ambiguous"] += 1
            rows_out.append(
                {
                    "locator": locator,
                    "outcome": enums.RowOutcome.IDENTITY_AMBIGUOUS.value,
                    "candidate_count": len(views),
                }
            )
            continue
        if outcome == "CONFLICT":
            counts["conflict"] += 1
            rows_out.append(
                {"locator": locator, "outcome": enums.RowOutcome.IDENTITY_CONFLICT.value}
            )
            continue
        if outcome == "DUPLICATE":
            counts["duplicate"] += 1
            rows_out.append(
                {
                    "locator": locator,
                    "outcome": enums.RowOutcome.DUPLICATE_CANDIDATE.value,
                    "candidate_count": len(views),
                }
            )
            continue
        if _check_duplicate_external_id(db, business_id, core, product):
            counts["conflict"] += 1
            rows_out.append(
                {
                    "locator": locator,
                    "outcome": enums.RowOutcome.IDENTITY_CONFLICT.value,
                    "note": "external_id belongs to another product",
                }
            )
            continue
        if product is None:
            if product_limit is not None and (current_products + new_predicted) >= product_limit:
                counts["blocked"] += 1
                rows_out.append(
                    {
                        "locator": locator,
                        "outcome": enums.RowOutcome.BLOCKED.value,
                        "note": "entitlement_exceeded",
                    }
                )
                continue
            counts["new"] += 1
            new_predicted += 1
            rows_out.append({"locator": locator, "outcome": _OUTCOME_NEW})
        else:
            rows_out.append(
                {"locator": locator, "outcome": "MATCHED", "product_id": str(product.product_id)}
            )
    counts["read"] = read.row_count
    return {"complete": True, "headers": read.headers, "counts": counts, "rows": rows_out}
