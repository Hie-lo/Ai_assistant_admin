"""Celery execution for durable Phase 8 Sync Jobs."""
from __future__ import annotations

import uuid

from celery import shared_task

from app.application import entitlements, import_pipeline, sync_jobs
from app.application.mapping import active_mapping_for
from app.domain import enums
from app.domain.sync_policy import SyncPolicy
from app.infrastructure.db import models
from app.infrastructure.db.session import get_session_factory


@shared_task(name="sync.run_job", bind=True, max_retries=0)
def run_sync_job(self, sync_id: str) -> dict:
    """Claim and execute one durable SyncJob.

    Celery is only the delivery mechanism; SyncJob remains the source of
    truth. A task that is delivered twice can claim the job only once.
    """
    db = get_session_factory()()
    try:
        job = db.get(models.SyncJob, uuid.UUID(sync_id))
        if job is None:
            return {"sync_id": sync_id, "status": "NOT_FOUND"}
        if not sync_jobs.begin(db, job, policy=SyncPolicy(max_attempts=job.max_attempts)):
            db.commit()
            return {"sync_id": sync_id, "status": job.status}

        source = db.get(models.Source, job.source_id)
        if source is None or source.business_id != job.business_id:
            sync_jobs.retry_or_exhaust(
                db,
                job,
                error="source not found or business scope mismatch",
                policy=SyncPolicy(max_attempts=job.max_attempts),
            )
            db.commit()
            return {"sync_id": sync_id, "status": job.status}

        mapping = active_mapping_for(db, source)
        if mapping is None:
            sync_jobs.retry_or_exhaust(
                db,
                job,
                error="source has no active mapping",
                policy=SyncPolicy(max_attempts=job.max_attempts),
            )
            db.commit()
            return {"sync_id": sync_id, "status": job.status}

        entitlements.require_entitlement_unchecked(
            db, business_id=job.business_id
        )
        outcome = import_pipeline.run_import(
            db,
            source=source,
            business_id=job.business_id,
            actor_id=job.requested_by,
            correlation_id=job.correlation_id,
        )
        sync_jobs.finish(
            job,
            success=outcome.run.status
            in (
                enums.ImportRunStatus.SUCCEEDED.value,
                enums.ImportRunStatus.SUCCEEDED_WITH_ERRORS.value,
            ),
            counts=outcome.counts,
            row_errors=outcome.run.row_errors,
        )
        db.commit()
        return {"sync_id": sync_id, "status": job.status, "counts": job.counts}
    except entitlements.EntitlementDenied as exc:
        db.rollback()
        job = db.get(models.SyncJob, uuid.UUID(sync_id))
        if job is not None:
            sync_jobs.fail_final(db, job, error=str(exc))
            db.commit()
        return {"sync_id": sync_id, "status": enums.SyncJobStatus.FAILED_FINAL.value}
    except Exception as exc:  # noqa: BLE001 - durable retry policy handles it
        db.rollback()
        job = db.get(models.SyncJob, uuid.UUID(sync_id))
        if job is not None:
            sync_jobs.begin(db, job, policy=SyncPolicy(max_attempts=job.max_attempts))
            sync_jobs.retry_or_exhaust(
                db,
                job,
                error=f"{type(exc).__name__}: sync worker failure",
                policy=SyncPolicy(max_attempts=job.max_attempts),
            )
            db.commit()
        raise
    finally:
        db.close()
