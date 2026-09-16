"""Scheduler dispatch for configured Google Sheets sources."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from celery import shared_task
from sqlalchemy import select

from app.application import sync_jobs
from app.domain import enums
from app.infrastructure.db import models
from app.infrastructure.db.session import get_session_factory


@shared_task(name="sync.dispatch_due_sources")
def dispatch_due_sources() -> dict[str, int]:
    db = get_session_factory()()
    created = 0
    coalesced = 0
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
            else:
                coalesced += 1
            # Prevent every beat tick from creating a new request while the
            # job is queued; the authoritative last_sync_at is updated by the
            # completed import, not by dispatch.
            _ = job
        db.commit()
        return {"created": created, "coalesced": coalesced}
    finally:
        db.close()
