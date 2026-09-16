"""Scheduler dispatch for configured Google Sheets sources."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from celery import shared_task
from sqlalchemy import select

from app.application import entitlements, sync_jobs
from app.domain import enums
from app.infrastructure.db import models
from app.infrastructure.db.session import get_session_factory
from app.workers.sync_tasks import run_sync_job


@shared_task(name="sync.dispatch_due_sources")
def dispatch_due_sources() -> dict[str, int]:
    db = get_session_factory()()
    created = 0
    coalesced = 0
    dispatch_ids: list[str] = []
    try:
        now = datetime.now(UTC)
        sources = db.scalars(
            select(models.Source).where(
                models.Source.status == enums.SourceStatus.ACTIVE.value,
                models.Source.kind == enums.SourceKind.GOOGLE_SHEETS.value,
                models.Source.automatic_sync_enabled.is_(True),
            )
        ).all()
        for source in sources:
            due_at = (
                source.last_sync_at + timedelta(minutes=source.sync_interval_minutes)
                if source.last_sync_at
                else now
            )
            if due_at > now:
                continue
            # Do not create work that cannot legally execute. The Worker
            # rechecks immediately before the external read as a second gate.
            effective = entitlements.get_entitlements(
                db, business_id=source.business_id
            )
            if not effective.has_active:
                continue
            mapping = db.scalar(
                select(models.SourceMapping).where(
                    models.SourceMapping.source_id == source.source_id,
                    models.SourceMapping.status == enums.MappingStatus.ACTIVE.value,
                )
            )
            if mapping is None:
                continue
            job, was_created = sync_jobs.enqueue(
                db,
                source=source,
                mapping=mapping,
                trigger=enums.SyncTrigger.SCHEDULED,
                requested_by=None,
                correlation_id=f"scheduled-{source.source_id}-{now:%Y%m%d%H%M}",
            )
            if was_created:
                created += 1
                dispatch_ids.append(str(job.sync_id))
            else:
                coalesced += 1
        db.commit()
        # Dispatch only after commit: workers must never observe an
        # uncommitted SyncJob and report NOT_FOUND.
        for sync_id in dispatch_ids:
            run_sync_job.delay(sync_id)
        return {"created": created, "coalesced": coalesced}
    finally:
        db.close()
