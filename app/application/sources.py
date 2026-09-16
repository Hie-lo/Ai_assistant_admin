"""Source application service: CRUD (entitlement-gated), mapping lifecycle,
import triggers.

All operations are business-scoped and audited.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application.audit import AuditService
from app.domain import enums
from app.infrastructure.db import models


def source_count(db: Session, business_id: uuid.UUID, *, active_only: bool = False) -> int:
    from sqlalchemy import func

    stmt = select(func.count()).select_from(models.Source).where(
        models.Source.business_id == business_id
    )
    if active_only:
        stmt = stmt.where(models.Source.status.in_(
            [enums.SourceStatus.ACTIVE.value, enums.SourceStatus.PAUSED.value]
        ))
    return int(db.scalar(stmt) or 0)


def get_source(db: Session, business_id: uuid.UUID, source_id: uuid.UUID) -> models.Source | None:
    return db.scalar(
        select(models.Source).where(
            models.Source.source_id == source_id,
            models.Source.business_id == business_id,
        )
    )


def list_sources(db: Session, business_id: uuid.UUID) -> list[models.Source]:
    return list(
        db.scalars(
            select(models.Source)
            .where(models.Source.business_id == business_id)
            .order_by(models.Source.created_at.desc())
        )
    )


def _source_limit(db: Session, business_id: uuid.UUID) -> int | None:
    from app.application.entitlements import get_entitlements

    ent = get_entitlements(db, business_id=business_id)
    return ent.source_limit


def _extract_sheet_id_from_url(ref: str | None) -> str | None:
    """Customer-friendly: if user pastes full Google Sheets URL, extract ID."""
    if not ref:
        return None
    ref = ref.strip()
    if not ref:
        return None
    # If it looks like a URL containing /d/, extract ID
    import re

    m = re.search(r"/d/([a-zA-Z0-9-_]+)", ref)
    if m:
        return m.group(1)
    # Otherwise return as-is (might already be ID, or Excel path etc)
    # Strip query params if ID with ?usp...
    if "?" in ref and "/" not in ref:
        ref = ref.split("?")[0]
    return ref.strip() or None


def create_source(
    db: Session,
    *,
    business_id: uuid.UUID,
    actor_id: uuid.UUID,
    correlation_id: str,
    name: str,
    kind: enums.SourceKind,
    external_ref: str | None = None,
    sheet_name: str | None = None,
    range_spec: str | None = None,
    credentials_ref: str | None = None,
    media_authoritative: bool = False,
) -> models.Source:
    limit = _source_limit(db, business_id)
    if limit is not None and source_count(db, business_id) >= limit:
        raise PermissionError("source entitlement limit reached")

    # Customer-friendly: auto-extract spreadsheet ID from full URL for Google Sheets
    normalized_ref = external_ref
    if kind == enums.SourceKind.GOOGLE_SHEETS and external_ref:
        normalized_ref = _extract_sheet_id_from_url(external_ref)

    source = models.Source(
        business_id=business_id,
        name=name,
        kind=kind.value,
        external_ref=normalized_ref or None,
        sheet_name=sheet_name or None,
        range_spec=range_spec or None,
        credentials_ref=credentials_ref or None,
        media_authoritative=media_authoritative,
        status=enums.SourceStatus.PENDING_MAPPING.value,
    )
    db.add(source)
    db.flush()
    AuditService(db).record(
        action="source.created",
        actor_user_id=actor_id,
        business_id=business_id,
        target_type="source",
        target_id=str(source.source_id),
        meta={"kind": kind.value, "name": name},
    )
    db.flush()
    return source


def set_source_status(
    db: Session,
    *,
    business_id: uuid.UUID,
    source: models.Source,
    actor_id: uuid.UUID,
    correlation_id: str,
    new_status: enums.SourceStatus,
) -> models.Source:
    if new_status == source.status:
        return source
    source.status = new_status.value
    AuditService(db).record(
        action=f"source.{new_status.value.lower()}",
        actor_user_id=actor_id,
        business_id=business_id,
        target_type="source",
        target_id=str(source.source_id),
        meta={"status": new_status.value},
    )
    db.flush()
    return source


def list_mappings(db: Session, source: models.Source) -> list[models.SourceMapping]:
    return list(
        db.scalars(
            select(models.SourceMapping)
            .where(models.SourceMapping.source_id == source.source_id)
            .order_by(models.SourceMapping.version.desc())
        )
    )


def get_mapping(
    db: Session, source: models.Source, mapping_id: uuid.UUID
) -> models.SourceMapping | None:
    return db.scalar(
        select(models.SourceMapping).where(
            models.SourceMapping.mapping_id == mapping_id,
            models.SourceMapping.source_id == source.source_id,
        )
    )


def list_import_runs(
    db: Session, business_id: uuid.UUID, source_id: uuid.UUID | None = None
) -> list[models.ImportRun]:
    stmt = select(models.ImportRun).where(models.ImportRun.business_id == business_id)
    if source_id is not None:
        stmt = stmt.where(models.ImportRun.source_id == source_id)
    return list(db.scalars(stmt.order_by(models.ImportRun.started_at.desc()).limit(100)))
