"""Periodic recovery/re-dispatch for durable SyncJobs."""
from __future__ import annotations

from datetime import UTC, datetime

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
    dispatch_ids: list[str] = []
    try:
        now = datetime.now(UTC)
        jobs = db.scalars(
            select(models.SyncJob).where(
                models.SyncJob.status.in_(
                    [
                        enums.SyncJobStatus.RETRY_WAITING.value,
                        enums.SyncJobStatus.RUNNING.value,
                    ]
                )
            )
        ).all()
        for job in jobs:
            policy = SyncPolicy(max_attempts=job.max_attempts)
            if job.status == enums.SyncJobStatus.RETRY_WAITING.value:
                if job.next_retry_at is not None and job.next_retry_at <= now:
                    job.status = enums.SyncJobStatus.QUEUED.value
                    job.next_retry_at = None
                    redispatched += 1
                    dispatch_ids.append(str(job.sync_id))
                continue
            if job.heartbeat_at is None or now - job.heartbeat_at <= policy.stale_delta():
                continue
            # A stale worker is treated as a failed attempt. The durable
            # state decides whether another attempt or final recovery is safe.
            sync_jobs.retry_or_exhaust(
                db,
                job,
                error="worker heartbeat expired; recovery dispatcher reclaimed job",
                policy=policy,
                now=now,
            )
            recovered += 1
        db.commit()
        for sync_id in dispatch_ids:
            run_sync_job.delay(sync_id)
        return {"redispatched": redispatched, "recovered": recovered}
    finally:
        db.close()
