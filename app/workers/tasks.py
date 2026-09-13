"""Background tasks (Phase 2: subscription cycle processing).

Each task opens its own database session (never a request-scoped one),
commits its own work, and is idempotent: re-running the same task does not
double-apply transitions (spec section 4: explicit, auditable transitions;
failure-first rule: queued jobs re-evaluate before external side effects —
these transitions have none, they only move internal state).
"""

from __future__ import annotations

from celery import shared_task

from app.application import subscription as subscription_use


@shared_task(name="billing.process_subscription_cycles", bind=True, max_retries=3)
def process_subscription_cycles(self) -> dict[str, int]:
    """Hourly: move ACTIVE past-period -> GRACE, GRACE past-grace -> EXPIRED.

    Retries are bounded (3) with the default exponential backoff; the work
    itself is idempotent, so a retry after a partial commit is safe.
    """
    try:
        return subscription_use.run_cycle_task()
    except Exception as exc:  # noqa: BLE001 - bounded retry, then alert via result
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries)) from exc
