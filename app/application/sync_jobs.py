"""Durable Sync Job lifecycle and coalescing.

This module owns orchestration state only. Product mutation remains in the
existing import pipeline and is never duplicated here.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import enums
from app.domain.sync_policy import SyncPolicy
from app.infrastructure.db import models


def _key(source_id: uuid.UUID, mapping_id: uuid.UUID | None, bucket: str) -> str:
    raw = f"{source_id}:{mapping_id or ''}:{bucket}".encode()
    return hashlib.sha256(raw).hexdigest()


def _active_job(db: Session, source_id: uuid.UUID) -> models.SyncJob | None:
    return db.scalar(
        select(models.SyncJob)
        .where(
            models.SyncJob.source_id == source_id,
            models.SyncJob.status.in_(
                [
                    enums.SyncJobStatus.QUEUED.value,
                    enums.SyncJobStatus.RUNNING.value,
                    enums.SyncJobStatus.RETRY_WAITING.value,
                ]
            ),
        )
        .order_by(models.SyncJob.created_at.asc())
        .with_for_update()
    )


def enqueue(
    db: Session,
    *,
    source: models.Source,
    mapping: models.SourceMapping | None,
    trigger: enums.SyncTrigger,
    requested_by: uuid.UUID | None,
    correlation_id: str,
    policy: SyncPolicy | None = None,
    bucket: str | None = None,
) -> tuple[models.SyncJob, bool]:
    """Create one job or coalesce onto the existing active Source job.

    Returns ``(job, created)``. The row lock is the database-side guard;
    the unique idempotency key is the durable backstop.
    """
    policy = policy or SyncPolicy()
    if (
        trigger != enums.SyncTrigger.MANUAL
        and source.kind == enums.SourceKind.EXCEL_UPLOAD.value
    ):
        raise ValueError(
            "scheduled and automatic sync are supported only for Google Sheets sources"
        )
    active = _active_job(db, source.source_id)
    if active is not None:
        triggers = list(active.coalesced_triggers or [])
        trigger_value = trigger.value
        if trigger_value not in triggers:
            triggers.append(trigger_value)
        active.coalesced_triggers = triggers
        return active, False

    idempotency = _key(
        source.source_id,
        mapping.mapping_id if mapping else None,
        bucket or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M"),
    )
    job = models.SyncJob(
        source_id=source.source_id,
        business_id=source.business_id,
        mapping_version_id=mapping.mapping_id if mapping else None,
        status=enums.SyncJobStatus.QUEUED.value,
        trigger=trigger.value,
        coalesced_triggers=[trigger.value],
        requested_by=requested_by,
        idempotency_key=idempotency,
        attempt_count=0,
        max_attempts=policy.max_attempts,
        correlation_id=correlation_id,
        counts={},
        row_errors=[],
    )
    db.add(job)
    db.flush()
    return job, True


def begin(db: Session, job: models.SyncJob, *, policy: SyncPolicy | None = None) -> bool:
    """Atomically claim a queued/retryable job for one worker."""
    if job.status not in (
        enums.SyncJobStatus.QUEUED.value,
        enums.SyncJobStatus.RETRY_WAITING.value,
    ):
        return False
    policy = policy or SyncPolicy(max_attempts=job.max_attempts)
    if job.attempt_count >= policy.max_attempts:
        job.status = enums.SyncJobStatus.FAILED_RETRY_EXHAUSTED.value
        job.finished_at = datetime.now(UTC)
        return False
    now = datetime.now(UTC)
    job.status = enums.SyncJobStatus.RUNNING.value
    job.attempt_count += 1
    job.started_at = job.started_at or now
    job.heartbeat_at = now
    job.next_retry_at = None
    return True


def retry_or_exhaust(
    db: Session,
    job: models.SyncJob,
    *,
    error: str,
    policy: SyncPolicy | None = None,
    now: datetime | None = None,
) -> bool:
    """Record a failure. Return True when another attempt is allowed."""
    policy = policy or SyncPolicy(max_attempts=job.max_attempts)
    now = now or datetime.now(UTC)
    job.failure_summary = error[:1000]
    job.heartbeat_at = None
    if policy.exhausted(job.attempt_count):
        job.status = enums.SyncJobStatus.FAILED_RETRY_EXHAUSTED.value
        job.finished_at = now
        return False
    job.status = enums.SyncJobStatus.RETRY_WAITING.value
    job.next_retry_at = now + policy.backoff(job.attempt_count)
    return True


def heartbeat(job: models.SyncJob, *, now: datetime | None = None) -> None:
    if job.status == enums.SyncJobStatus.RUNNING.value:
        job.heartbeat_at = now or datetime.now(UTC)


def finish(
    job: models.SyncJob,
    *,
    success: bool,
    counts: dict,
    row_errors: list,
    now: datetime | None = None,
) -> None:
    now = now or datetime.now(UTC)
    job.counts = counts
    job.row_errors = row_errors
    job.heartbeat_at = None
    job.finished_at = now
    job.status = (
        enums.SyncJobStatus.SUCCEEDED_WITH_ERRORS.value
        if row_errors
        else enums.SyncJobStatus.SUCCEEDED.value
    ) if success else enums.SyncJobStatus.RECOVERY_REQUIRED.value
