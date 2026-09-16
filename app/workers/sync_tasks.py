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

    Failure-first:
    - Entitlement is checked BEFORE claiming attempt count
    - Source scope + mapping checked before expensive work
    - Heartbeat is updated during import for long-running jobs
    - RETRY_WAITING is recovered by sync.recover_jobs, QUEUED stuck jobs
      are also recovered there
    """
    db = get_session_factory()()
    try:
        job = db.get(models.SyncJob, uuid.UUID(sync_id))
        if job is None:
            return {"sync_id": sync_id, "status": "NOT_FOUND"}

        # Entitlement gate BEFORE consuming an attempt (don't burn retries
        # on billing issues)
        try:
            entitlements.require_entitlement_unchecked(
                db, business_id=job.business_id
            )
        except entitlements.EntitlementDenied as exc:
            # No attempt consumed yet, go straight to final failure
            sync_jobs.fail_final(db, job, error=str(exc))
            db.commit()
            return {
                "sync_id": sync_id,
                "status": enums.SyncJobStatus.FAILED_FINAL.value,
            }

        # Now claim the job (consumes one attempt)
        if not sync_jobs.begin(
            db, job, policy=SyncPolicy(max_attempts=job.max_attempts)
        ):
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

        # Heartbeat before expensive work
        sync_jobs.heartbeat(job)
        db.flush()

        outcome = import_pipeline.run_import(
            db,
            source=source,
            business_id=job.business_id,
            actor_id=job.requested_by,
            correlation_id=job.correlation_id,
        )

        # Heartbeat after import (long imports could have been considered
        # stale if we didn't update heartbeat)
        sync_jobs.heartbeat(job)
        db.flush()

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
        return {
            "sync_id": sync_id,
            "status": job.status,
            "counts": job.counts,
        }
    except entitlements.EntitlementDenied as exc:
        # Entitlement revoked between initial check and import
        try:
            db.rollback()
            j = db.get(models.SyncJob, uuid.UUID(sync_id))
            if j is not None:
                sync_jobs.fail_final(db, j, error=str(exc))
                db.commit()
        except Exception:
            db.rollback()
        return {
            "sync_id": sync_id,
            "status": enums.SyncJobStatus.FAILED_FINAL.value,
        }
    except Exception as exc:  # noqa: BLE001 - durable retry policy handles it
        try:
            db.rollback()
            j = db.get(models.SyncJob, uuid.UUID(sync_id))
            if j is not None:
                sync_jobs.retry_or_exhaust(
                    db,
                    j,
                    error=f"{type(exc).__name__}: sync worker failure",
                    policy=SyncPolicy(max_attempts=j.max_attempts),
                )
                db.commit()
        except Exception:
            db.rollback()
        # Don't re-raise: task is max_retries=0, raising would mark Celery
        # task as failed while DB already has RETRY_WAITING state.
        # Recovery dispatcher will redispatch when next_retry_at is due.
        return {
            "sync_id": sync_id,
            "status": "RETRY_SCHEDULED",
            "error": type(exc).__name__,
        }
    finally:
        db.close()
