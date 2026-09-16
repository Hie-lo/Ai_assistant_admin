"""Periodic recovery/re-dispatch for durable SyncJobs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from celery import shared_task
from sqlalchemy import select

from app.application import sync_jobs
from app.domain import enums
from app.domain.sync_policy import SyncPolicy
from app.infrastructure.db import models
from app.infrastructure.db.session import get_session_factory
from app.workers.sync_tasks import run_sync_job


@shared_task(name="sync.recover_jobs")
def recover_jobs() -> dict[str, int]:
    db = get_session_factory()()
    redispatched = 0
    recovered = 0
    queued_recovered = 0
    dispatch_ids: list[str] = []
    try:
        now = datetime.now(UTC)
        jobs = db.scalars(
            select(models.SyncJob).where(
                models.SyncJob.status.in_(
                    [
                        enums.SyncJobStatus.RETRY_WAITING.value,
                        enums.SyncJobStatus.RUNNING.value,
                        enums.SyncJobStatus.QUEUED.value,
                        enums.SyncJobStatus.RECOVERY_REQUIRED.value,
                    ]
                )
            )
        ).all()

        for job in jobs:
            policy = SyncPolicy(max_attempts=job.max_attempts)

            if job.status == enums.SyncJobStatus.QUEUED.value:
                # QUEUED jobs that never got dispatched (broker down) —
                # if older than 2 minutes, redispatch. This prevents stuck
                # QUEUED jobs per spec section 23 (recovery after crash).
                age = now - job.created_at if job.created_at else timedelta(0)
                if age > timedelta(minutes=2):
                    dispatch_ids.append(str(job.sync_id))
                    queued_recovered += 1
                continue

            if job.status == enums.SyncJobStatus.RETRY_WAITING.value:
                if job.next_retry_at is not None and job.next_retry_at <= now:
                    job.status = enums.SyncJobStatus.QUEUED.value
                    job.next_retry_at = None
                    redispatched += 1
                    dispatch_ids.append(str(job.sync_id))
                continue

            if job.status == enums.SyncJobStatus.RECOVERY_REQUIRED.value:
                # Import failed (e.g., incomplete read) — treat as retryable
                # failure, not stuck. Notify and schedule retry if attempts
                # remain.
                sync_jobs.retry_or_exhaust(
                    db,
                    job,
                    error=job.failure_summary
                    or "recovery required after failed import",
                    policy=policy,
                    now=now,
                )
                recovered += 1
                if job.status == enums.SyncJobStatus.QUEUED.value:
                    dispatch_ids.append(str(job.sync_id))
                continue

            # RUNNING with expired heartbeat -> reclaim
            if job.heartbeat_at is None:
                # No heartbeat ever set (very old job) — treat as stale
                stale = True
            else:
                stale = (now - job.heartbeat_at) > policy.stale_delta()

            if not stale:
                continue

            sync_jobs.retry_or_exhaust(
                db,
                job,
                error="worker heartbeat expired; recovery reclaimed job",
                policy=policy,
                now=now,
            )
            recovered += 1
            if job.status == enums.SyncJobStatus.QUEUED.value:
                dispatch_ids.append(str(job.sync_id))

        db.commit()
        for sync_id in dispatch_ids:
            run_sync_job.delay(sync_id)

        return {
            "redispatched": redispatched,
            "recovered": recovered,
            "queued_recovered": queued_recovered,
        }
    finally:
        db.close()
