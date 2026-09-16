"""Phase 8 routes: durable Sync Jobs + Notifications."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from datetime import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.application import entitlements, sync_jobs
from app.application.audit import get_correlation_id
from app.application.mapping import active_mapping_for
from app.application.notifications import mark_read
from app.domain import enums
from app.domain.errors import (
    BusinessNotAccessible,
    ConflictError,
    EntitlementDenied,
    ValidationError,
)
from app.domain.permissions import (
    SCHEDULE_MANAGE,
    SCHEDULE_VIEW,
    SYNC_RUN_MANUAL,
    SYNC_VIEW,
)
from app.domain.sync_policy import SyncPolicy
from app.infrastructure.db import models
from app.interfaces.http.deps import CurrentUser, Db, require_business_permission

router = APIRouter(prefix="/api/v1/businesses/{business_id}", tags=["sync"])

_SyncView = Annotated[object, Depends(require_business_permission(SYNC_VIEW))]
_SyncRun = Annotated[object, Depends(require_business_permission(SYNC_RUN_MANUAL))]
_ScheduleView = Annotated[object, Depends(require_business_permission(SCHEDULE_VIEW))]
_ScheduleManage = Annotated[
    object, Depends(require_business_permission(SCHEDULE_MANAGE))
]


def _require_source(
    db, business_id: uuid.UUID, source_id: uuid.UUID
) -> models.Source:
    src = db.scalar(
        select(models.Source).where(
            models.Source.source_id == source_id,
            models.Source.business_id == business_id,
        )
    )
    if src is None:
        raise BusinessNotAccessible()
    return src


class SyncJobResponse(BaseModel):
    sync_id: uuid.UUID
    source_id: uuid.UUID
    business_id: uuid.UUID
    status: str
    trigger: str
    coalesced_triggers: list[str]
    attempt_count: int
    max_attempts: int
    correlation_id: str
    counts: dict
    row_errors: list
    failure_summary: str | None
    started_at: dt | None
    finished_at: dt | None
    next_retry_at: dt | None
    created_at: dt
    mapping_version_id: uuid.UUID | None = None

    class Config:
        from_attributes = True


class SyncTriggerRequest(BaseModel):
    trigger: str = Field(default="MANUAL", pattern="^(MANUAL|SCHEDULED)$")


class NotificationOut(BaseModel):
    notification_id: uuid.UUID
    business_id: uuid.UUID
    recipient_user_id: uuid.UUID
    kind: str
    status: str
    title: str
    body: str
    data: dict
    correlation_id: str
    created_at: dt
    read_at: dt | None = None

    class Config:
        from_attributes = True


class ScheduleUpdateRequest(BaseModel):
    sync_interval_minutes: int | None = Field(
        default=None, ge=5, le=10080
    )
    automatic_sync_enabled: bool | None = None


@router.post(
    "/sources/{source_id}/sync",
    response_model=SyncJobResponse,
    status_code=202,
)
def trigger_sync(
    business_id: uuid.UUID,
    source_id: uuid.UUID,
    user: CurrentUser,
    db: Db,
    _scope: _SyncRun,
    body: SyncTriggerRequest | None = None,
):
    source = _require_source(db, business_id, source_id)
    trigger_str = body.trigger if body else "MANUAL"
    try:
        trigger = enums.SyncTrigger(trigger_str)
    except ValueError as exc:
        raise ValidationError(
            f"invalid trigger: {trigger_str}"
        ) from exc

    if (
        trigger != enums.SyncTrigger.MANUAL
        and source.kind == enums.SourceKind.EXCEL_UPLOAD.value
    ):
        raise ValidationError(
            "scheduled and automatic sync are supported only for "
            "Google Sheets sources"
        )

    try:
        entitlements.require_entitlement_unchecked(
            db, business_id=business_id
        )
    except EntitlementDenied as exc:
        raise exc

    mapping = active_mapping_for(db, source)
    if mapping is None:
        raise ValidationError("source has no active mapping")

    today_start = datetime.now(UTC).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    count = int(
        db.scalar(
            select(func.count())
            .select_from(models.ImportRun)
            .where(
                models.ImportRun.business_id == business_id,
                models.ImportRun.started_at >= today_start,
            )
        )
        or 0
    )
    ent = entitlements.get_entitlements(db, business_id=business_id)
    if (
        ent.sync_frequency_per_day is not None
        and count >= ent.sync_frequency_per_day
    ):
        raise EntitlementDenied(
            f"daily sync limit reached ({ent.sync_frequency_per_day} per day)"
        )

    correlation = get_correlation_id()
    try:
        job, created = sync_jobs.enqueue(
            db,
            source=source,
            mapping=mapping,
            trigger=trigger,
            requested_by=user.user_id,
            correlation_id=correlation,
            policy=SyncPolicy(),
        )
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc

    db.flush()

    if created:
        try:
            from app.workers.sync_tasks import run_sync_job

            run_sync_job.delay(str(job.sync_id))
        except Exception:
            pass

    return SyncJobResponse.model_validate(job)


@router.get("/sync-jobs", response_model=list[SyncJobResponse])
def list_sync_jobs(
    business_id: uuid.UUID,
    db: Db,
    _scope: _SyncView,
    source_id: uuid.UUID | None = None,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
):
    stmt = select(models.SyncJob).where(
        models.SyncJob.business_id == business_id
    )
    if source_id is not None:
        stmt = stmt.where(models.SyncJob.source_id == source_id)
    if status is not None:
        try:
            enums.SyncJobStatus(status)
        except ValueError as exc:
            raise ValidationError(f"invalid status: {status}") from exc
        stmt = stmt.where(models.SyncJob.status == status)
    stmt = stmt.order_by(models.SyncJob.created_at.desc()).limit(limit)
    jobs = db.scalars(stmt).all()
    return [SyncJobResponse.model_validate(j) for j in jobs]


@router.get("/sync-jobs/{sync_id}", response_model=SyncJobResponse)
def get_sync_job(
    business_id: uuid.UUID,
    sync_id: uuid.UUID,
    db: Db,
    _scope: _SyncView,
):
    job = db.get(models.SyncJob, sync_id)
    if job is None or job.business_id != business_id:
        raise BusinessNotAccessible()
    return SyncJobResponse.model_validate(job)


@router.post("/sync-jobs/{sync_id}/cancel", response_model=SyncJobResponse)
def cancel_sync_job(
    business_id: uuid.UUID,
    sync_id: uuid.UUID,
    user: CurrentUser,
    db: Db,
    _scope: _SyncRun,
):
    job = db.get(models.SyncJob, sync_id)
    if job is None or job.business_id != business_id:
        raise BusinessNotAccessible()
    if job.status not in (
        enums.SyncJobStatus.QUEUED.value,
        enums.SyncJobStatus.RETRY_WAITING.value,
    ):
        raise ConflictError(f"cannot cancel job in status {job.status}")
    job.status = enums.SyncJobStatus.CANCELLED.value
    job.finished_at = datetime.now(UTC)
    db.flush()
    return SyncJobResponse.model_validate(job)


@router.get("/sources/{source_id}/schedule")
def get_schedule(
    business_id: uuid.UUID,
    source_id: uuid.UUID,
    db: Db,
    _scope: _ScheduleView,
):
    source = _require_source(db, business_id, source_id)
    return {
        "source_id": str(source.source_id),
        "kind": source.kind,
        "sync_interval_minutes": source.sync_interval_minutes,
        "automatic_sync_enabled": source.automatic_sync_enabled,
        "last_sync_at": source.last_sync_at.isoformat()
        if source.last_sync_at
        else None,
    }


@router.patch("/sources/{source_id}/schedule")
def update_schedule(
    business_id: uuid.UUID,
    source_id: uuid.UUID,
    user: CurrentUser,
    db: Db,
    _scope: _ScheduleManage,
    body: ScheduleUpdateRequest,
):
    source = _require_source(db, business_id, source_id)
    if (
        source.kind == enums.SourceKind.EXCEL_UPLOAD.value
        and body.automatic_sync_enabled
    ):
        raise ValidationError(
            "automatic sync is supported only for Google Sheets sources"
        )
    if body.sync_interval_minutes is not None:
        source.sync_interval_minutes = body.sync_interval_minutes
    if body.automatic_sync_enabled is not None:
        source.automatic_sync_enabled = body.automatic_sync_enabled
    db.flush()
    return {
        "source_id": str(source.source_id),
        "sync_interval_minutes": source.sync_interval_minutes,
        "automatic_sync_enabled": source.automatic_sync_enabled,
    }


@router.get("/notifications", response_model=list[NotificationOut])
def list_notifications(
    business_id: uuid.UUID,
    db: Db,
    user: CurrentUser,
    _scope: _SyncView,
    unread_only: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
):
    stmt = select(models.Notification).where(
        models.Notification.business_id == business_id,
        models.Notification.recipient_user_id == user.user_id,
    )
    if unread_only:
        stmt = stmt.where(
            models.Notification.status
            == enums.NotificationStatus.UNREAD.value
        )
    stmt = stmt.order_by(models.Notification.created_at.desc()).limit(limit)
    notifs = db.scalars(stmt).all()
    return [NotificationOut.model_validate(n) for n in notifs]


@router.post("/notifications/{notification_id}/read")
def read_notification(
    business_id: uuid.UUID,
    notification_id: uuid.UUID,
    user: CurrentUser,
    db: Db,
    _scope: _SyncView,
):
    notif = db.get(models.Notification, notification_id)
    if notif is None or notif.business_id != business_id:
        raise BusinessNotAccessible()
    ok = mark_read(
        db, notification_id=notification_id, user_id=user.user_id
    )
    if not ok:
        raise BusinessNotAccessible()
    db.flush()
    return {"notification_id": str(notification_id), "status": "READ"}
