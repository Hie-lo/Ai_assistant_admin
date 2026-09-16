"""Celery application factory.

The broker is Redis; durable business/job state lives in PostgreSQL. Tasks
are registered by importing their modules (``app.workers.tasks``) which will
be added phase by phase. Bounded retries, idempotency keys, and per-platform
throttling are enforced at the task level per the failure-first rules.
"""

from __future__ import annotations

from celery import Celery

from app.config.settings import get_settings

settings = get_settings()

celery = Celery(
    settings.app_name,
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "app.workers.tasks",
        "app.workers.sync_tasks",
        "app.workers.sync_scheduler",
        "app.workers.sync_recovery",
        "app.workers.backup_tasks",
        "app.workers.publication_recovery",
    ],
)

celery.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # Deterministic, bounded behavior: no unbounded auto-retry. Individual
    # tasks set their own ``retry``/``autoretry_for`` with explicit bounds.
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    # Subscription cycle processing: hourly is enough for day-granularity
    # billing (periods end at day boundaries; grace is 7 days).
    beat_schedule={
        "subscription-cycles-hourly": {
            "task": "billing.process_subscription_cycles",
            "schedule": 3600.0,
        },
        "sync-dispatch-due-sources": {
            "task": "sync.dispatch_due_sources",
            "schedule": 60.0,
        },
        "sync-recover-jobs": {
            "task": "sync.recover_jobs",
            "schedule": 60.0,
        },
        "backup-daily": {
            "task": "backup.create_daily",
            "schedule": 86400.0,  # Daily
        },
        "publications-recover-unknown": {
            "task": "publications.recover_unknown",
            "schedule": 3600.0,  # Hourly auto-reconcile UNKNOWN
        },
    },
)
