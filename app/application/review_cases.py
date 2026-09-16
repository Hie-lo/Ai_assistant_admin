"""Review case application service: listing + human resolutions.

Resolutions are the ONLY path by which a quarantined row reaches a product
(ambiguous/conflicting identity are never auto-merged, spec section 17).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application.audit import AuditService
from app.domain import enums
from app.infrastructure.db import models

# Actions allowed per case kind.
_ALLOWED_ACTIONS = {
    enums.ReviewCaseKind.IDENTITY_AMBIGUOUS.value: ("MERGE_TO", "CREATE_NEW", "DISMISS"),
    enums.ReviewCaseKind.DUPLICATE_CANDIDATE.value: ("MERGE_TO", "CREATE_NEW", "DISMISS"),
    enums.ReviewCaseKind.IDENTITY_CONFLICT.value: ("KEEP_EXISTING", "REASSIGN_ID", "DISMISS"),
    enums.ReviewCaseKind.SUSPICIOUS_CHANGE.value: ("APPLY_BY_RESYNC", "DISMISS"),
    enums.ReviewCaseKind.MASS_MISSING_BLOCKED.value: ("ACKNOWLEDGE", "DISMISS"),
}


def list_cases(
    db: Session,
    business_id: uuid.UUID,
    *,
    status: enums.ReviewCaseStatus | None = None,
    kind: enums.ReviewCaseKind | None = None,
) -> list[models.ReviewCase]:
    stmt = select(models.ReviewCase).where(models.ReviewCase.business_id == business_id)
    if status is not None:
        stmt = stmt.where(models.ReviewCase.status == status.value)
    if kind is not None:
        stmt = stmt.where(models.ReviewCase.kind == kind.value)
    return list(db.scalars(stmt.order_by(models.ReviewCase.created_at.desc()).limit(200)))


def get_case(db: Session, business_id: uuid.UUID, case_id: uuid.UUID) -> models.ReviewCase | None:
    return db.scalar(
        select(models.ReviewCase).where(
            models.ReviewCase.case_id == case_id,
            models.ReviewCase.business_id == business_id,
        )
    )


def _apply_held_row(
    db: Session,
    *,
    business_id: uuid.UUID,
    case: models.ReviewCase,
    row: dict,
    target_product: models.Product | None,
    exclude_fields: tuple[str, ...] = (),
) -> models.Product | None:
    """Apply the held row (from the case payload) through the normal
    upsert path — with the conflicting identity fields excluded when
    requested."""
    from app.application import import_pipeline as pipeline
    from app.application.mapping import active_mapping_for

    source = db.get(models.Source, case.source_id) if case.source_id else None
    if source is None:
        return target_product
    mapping = active_mapping_for(db, source)
    if mapping is None:
        return target_product

    core, attrs, _errors = pipeline.extract_row(row, mapping)
    for f in exclude_fields:
        core.pop(f, None)
    core["_fingerprint"] = pipeline._fingerprint_for(core, attrs)
    content_hash = pipeline._content_hash_for(core, attrs, mapping)
    product, _version, _cats = pipeline._upsert_product(
        db,
        business_id=business_id,
        product=target_product,
        core=core,
        attrs=attrs,
        source=source,
        mapping=mapping,
        content_hash=content_hash,
    )
    # Reconnect (or create) the source record for this row.
    locator = row.get("_locator") or ""
    if locator and product is not None:
        record = db.scalar(
            select(models.SourceRecord).where(
                models.SourceRecord.source_id == source.source_id,
                models.SourceRecord.locator == locator,
            )
        )
        if record is None:
            record = models.SourceRecord(
                source_id=source.source_id,
                business_id=business_id,
                locator=locator,
                state="PRESENT",
                last_seen_at=datetime.now(UTC),
            )
            db.add(record)
        record.product_id = product.product_id
        record.state = "PRESENT"
        record.last_seen_at = datetime.now(UTC)
    return product


def resolve_case(
    db: Session,
    *,
    business_id: uuid.UUID,
    case_id: uuid.UUID,
    actor_id: uuid.UUID,
    correlation_id: str,
    action: str,
    target_product_id: uuid.UUID | None = None,
) -> models.ReviewCase:
    case = get_case(db, business_id, case_id)
    if case is None:
        raise LookupError("review case not found")
    if case.status != enums.ReviewCaseStatus.OPEN.value:
        raise ValueError("case already resolved")
    allowed = _ALLOWED_ACTIONS.get(case.kind, ("DISMISS",))
    if action not in allowed:
        raise ValueError(f"action '{action}' not allowed for kind '{case.kind}'")

    payload = case.payload or {}
    row = dict(payload.get("row") or {})
    row["_locator"] = payload.get("locator", "")
    resolution = None

    if case.kind in (
        enums.ReviewCaseKind.IDENTITY_AMBIGUOUS.value,
        enums.ReviewCaseKind.DUPLICATE_CANDIDATE.value,
    ):
        if action == "MERGE_TO":
            if target_product_id is None:
                raise ValueError("MERGE_TO requires target_product_id")
            target = db.scalar(
                select(models.Product).where(
                    models.Product.product_id == target_product_id,
                    models.Product.business_id == business_id,
                )
            )
            if target is None:
                raise LookupError("target product not found")
            _apply_held_row(
                db,
                business_id=business_id,
                case=case,
                row=row,
                target_product=target,
            )
            case.product_id = target.product_id
            resolution = f"merged_into:{target_product_id}"
        elif action == "CREATE_NEW":
            _apply_held_row(
                db,
                business_id=business_id,
                case=case,
                row=row,
                target_product=None,
            )
            resolution = "created_new"
        else:
            resolution = "dismissed"

    elif case.kind == enums.ReviewCaseKind.IDENTITY_CONFLICT.value:
        # The "matched" product is the candidate matched by non-ID fields.
        target = None
        for cand in payload.get("candidates", []):
            matched_by = cand.get("matched_by", [])
            if any(m in ("sku", "barcode", "fingerprint") for m in matched_by):
                    target = db.scalar(
                        select(models.Product).where(
                            models.Product.product_id == uuid.UUID(cand["product_id"]),
                            models.Product.business_id == business_id,
                        )
                    )
                    break
        if action == "KEEP_EXISTING":
            # Apply the row data to the matched product WITHOUT the
            # conflicting identity field.
            _apply_held_row(
                db,
                business_id=business_id,
                case=case,
                row=row,
                target_product=target,
                exclude_fields=("external_id",),
            )
            if target is not None:
                case.product_id = target.product_id
            resolution = "keep_existing_identity"
        elif action == "REASSIGN_ID":
            ext = payload.get("external_id")
            if target is not None and ext:
                owner = db.scalar(
                    select(models.Product).where(
                        models.Product.business_id == business_id,
                        models.Product.external_id == ext,
                        models.Product.product_id != target.product_id,
                    )
                )
                if owner is not None:
                    owner.external_id = None
                target.external_id = ext
            resolution = "identity_reassigned"
        else:
            resolution = "dismissed"

    elif case.kind == enums.ReviewCaseKind.SUSPICIOUS_CHANGE.value:
        resolution = "apply_by_resync" if action == "APPLY_BY_RESYNC" else "dismissed"
    else:
        resolution = "acknowledged" if action == "ACKNOWLEDGE" else "dismissed"

    case.status = enums.ReviewCaseStatus.RESOLVED.value
    case.resolution = resolution
    case.resolved_by = actor_id
    case.resolved_at = datetime.now(UTC)
    AuditService(db).record(
        action="review_case.resolved",
        actor_user_id=actor_id,
        business_id=business_id,
        target_type="review_case",
        target_id=str(case.case_id),
        meta={"kind": case.kind, "action": action, "resolution": resolution},
    )
    db.flush()
    return case
