"""Recovery for UNKNOWN_REMOTE_STATE publications (Phase 12 fix).

Publications that end up in UNKNOWN_REMOTE_STATE due to timeout after a
likely remote success need reconciliation. Previously this was manual-only
(/check endpoint). This worker does best-effort auto-reconcile for
platforms that support inspect_remote (Telegram) after a cooldown period.

Platforms without inspect_remote (Bale) stay UNKNOWN until manual check
or until connection re-verification resumes them.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from celery import shared_task
from sqlalchemy import select

from app.domain import enums
from app.infrastructure.db import models
from app.infrastructure.db.session import get_session_factory
from app.infrastructure.platforms import base as platforms


@shared_task(name="publications.recover_unknown", bind=True, max_retries=0)
def recover_unknown_publications(self) -> dict:
    """Auto-reconcile UNKNOWN publications older than 1h where possible."""
    db = get_session_factory()()
    try:
        cutoff = datetime.now(UTC) - timedelta(hours=1)
        pubs = db.scalars(
            select(models.Publication).where(
                models.Publication.status
                == enums.PublicationStatus.UNKNOWN_REMOTE_STATE.value,
                models.Publication.updated_at < cutoff,
            )
        ).all()

        reconciled = 0
        skipped = 0
        failed = 0

        for pub in pubs:
            try:
                conn = db.get(models.PlatformConnection, pub.connection_id)
                if conn is None:
                    skipped += 1
                    continue
                caps = platforms.get_capabilities(conn.platform)
                if not caps.inspect_remote:
                    # Bale and similar: cannot auto-inspect, skip
                    skipped += 1
                    continue
                if not pub.remote_message_id:
                    skipped += 1
                    continue

                client = platforms.get_platform_client(conn.platform)
                try:
                    msg = client.get_message(
                        conn.platform_target_id, int(pub.remote_message_id)
                    )
                except platforms.PlatformError as exc:
                    if exc.code is enums.PublicationErrorCode.NOT_FOUND:
                        # Remote deleted externally
                        from app.application.publications import _transition

                        _transition(
                            pub, enums.PublicationStatus.REMOTE_DELETED
                        )
                        reconciled += 1
                        db.flush()
                    else:
                        failed += 1
                    continue

                if msg is None:
                    from app.application.publications import _transition

                    _transition(pub, enums.PublicationStatus.REMOTE_DELETED)
                else:
                    from app.application.publications import _transition

                    _transition(pub, enums.PublicationStatus.PUBLISHED)
                reconciled += 1
                db.flush()
            except Exception:
                failed += 1
                continue

        db.commit()
        return {
            "checked": len(pubs),
            "reconciled": reconciled,
            "skipped": skipped,
            "failed": failed,
        }
    except Exception:
        db.rollback()
        return {"checked": 0, "error": "worker_failed"}
    finally:
        db.close()
