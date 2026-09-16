"""Celery execution for durable Phase 8 Sync Jobs."""

from __future__ import annotations

import uuid

from celery import shared_task
from sqlalchemy import select

from app.application import entitlements, import_pipeline, sync_jobs
from app.application.mapping import active_mapping_for
from app.domain import enums
from app.domain.sync_classification import should_auto_update
from app.domain.sync_policy import SyncPolicy
from app.infrastructure.db import models
from app.infrastructure.db.session import get_session_factory


def _get_business_owner(db, business_id: uuid.UUID):
    """Get first active OWNER for a business (for audit of auto actions)."""
    membership = db.scalar(
        select(models.Membership)
        .where(
            models.Membership.business_id == business_id,
            models.Membership.role == enums.MembershipRole.OWNER.value,
            models.Membership.status == enums.MembershipStatus.ACTIVE.value,
        )
        .order_by(models.Membership.created_at.asc())
    )
    if membership is None:
        return None
    return db.get(models.User, membership.user_id)


def _auto_update_changed_products(db, job, source, outcome):
    """Hybrid auto-publish: LOW/MEDIUM auto-edit, HIGH/CRITICAL notify.

    Only runs when source.automatic_sync_enabled is True and import
    succeeded (or succeeded with errors). For each CHANGED product with
    LOW/MEDIUM risk, tries to auto-edit its live publications.
    """
    if not source.automatic_sync_enabled:
        return {"auto_updated": 0, "needs_review": 0, "skipped": 0}

    if outcome.run.status not in (
        enums.ImportRunStatus.SUCCEEDED.value,
        enums.ImportRunStatus.SUCCEEDED_WITH_ERRORS.value,
    ):
        return {"auto_updated": 0, "needs_review": 0, "skipped": 0}

    business = db.get(models.Business, job.business_id)
    if business is None:
        return {"auto_updated": 0, "needs_review": 0, "skipped": 0}

    owner = _get_business_owner(db, job.business_id)
    # If no owner, we can still proceed with system actor (audit with None)
    # but we try to use owner for audit trail.

    auto_updated = 0
    needs_review = 0
    skipped = 0

    # Collect changed products with risk
    changed_with_risk: list[tuple[uuid.UUID, str | None]] = []
    for rr in outcome.row_results:
        if rr.outcome != "CHANGED":
            continue
        if not rr.product_id:
            continue
        try:
            pid = uuid.UUID(rr.product_id)
        except Exception:
            continue
        changed_with_risk.append((pid, rr.risk))

    if not changed_with_risk:
        return {"auto_updated": 0, "needs_review": 0, "skipped": 0}

    # For notifications of high-risk changes, collect them
    high_risk_products: list[uuid.UUID] = []

    for product_id, risk in changed_with_risk:
        # Check if product still exists and is active
        product = db.get(models.Product, product_id)
        if product is None or product.business_id != job.business_id:
            skipped += 1
            continue
        if product.lifecycle_state != enums.ProductLifecycle.ACTIVE.value:
            # Frozen products (REVIEW_REQUIRED, SOURCE_INVALID, etc) are not
            # auto-updated
            skipped += 1
            continue

        # Hybrid decision: only LOW/MEDIUM auto
        if not should_auto_update(
            risk=risk, automatic_enabled=True
        ):
            high_risk_products.append(product_id)
            needs_review += 1
            continue

        # Find live publications for this product
        pubs = db.scalars(
            select(models.Publication).where(
                models.Publication.business_id == job.business_id,
                models.Publication.product_id == product_id,
                models.Publication.status
                == enums.PublicationStatus.PUBLISHED.value,
            )
        ).all()

        if not pubs:
            skipped += 1
            continue

        # Try to auto-edit each publication
        for pub in pubs:
            try:
                # Reuse existing publication update logic but with owner as actor
                # We call the low-level edit path to avoid permission checks
                # that would require actor membership (owner already has all perms)
                import hashlib

                from app.application import content_preview
                from app.application import publications as pub_svc
                from app.domain import publication as pubdomain
                from app.infrastructure.platforms import base as platforms

                def _fp(v: str) -> str:
                    return hashlib.sha256(
                        (v or "").encode("utf-8")
                    ).hexdigest()

                def _media_fp(urls: list[str]) -> str:
                    return hashlib.sha256(
                        "|".join(urls or []).encode("utf-8")
                    ).hexdigest()

                conn = db.get(
                    models.PlatformConnection, pub.connection_id
                )
                if conn is None:
                    continue
                if (
                    conn.status
                    != enums.PlatformConnectionStatus.VERIFIED.value
                ):
                    continue

                caps = platforms.get_capabilities(conn.platform)
                # Render new content
                preview = content_preview.preview_product(
                    db, business=business, product_id=product_id
                )
                if preview.get("blocked_reason"):
                    continue
                media = preview["media_urls"][: caps.media_group_max]
                text = preview["text"]
                caption_limit = caps.caption_limit_for(len(media))
                if media and len(text) > caption_limit:
                    preview = content_preview.preview_product(
                        db,
                        business=business,
                        product_id=product_id,
                        max_length=caption_limit,
                    )
                    if preview.get("blocked_reason"):
                        continue
                    text = preview["text"]

                old_version = db.get(
                    models.PostVersion, pub.post_version_id
                )
                if old_version is None:
                    continue
                old_snap = pubdomain.ContentSnapshot(
                    content_fingerprint=old_version.content_fingerprint,
                    media_fingerprint=old_version.media_fingerprint,
                )
                new_snap = pubdomain.ContentSnapshot(
                    content_fingerprint=_fp(text),
                    media_fingerprint=_media_fp(media),
                )
                plan = pubdomain.plan_update(
                    old_snap,
                    new_snap,
                    edit_supported=caps.edit_text and caps.edit_caption,
                )
                if plan is pubdomain.UpdatePlan.NOOP:
                    continue
                if plan is pubdomain.UpdatePlan.REPOST:
                    # In hybrid mode, REPOST needs manual review (avoid spam)
                    needs_review += 1
                    high_risk_products.append(product_id)
                    continue

                # EDIT path — perform auto edit
                if owner is None:
                    # No owner for audit, skip but count as needs review
                    needs_review += 1
                    continue

                result = pub_svc.update_publication(
                    db,
                    business=business,
                    actor=owner,
                    publication_id=pub.publication_id,
                )
                if result.get("updated"):
                    auto_updated += 1
                else:
                    skipped += 1

            except Exception:
                # One publication failure must not block others
                skipped += 1
                continue

    # Notify owner/admin about high-risk changes needing review
    if high_risk_products:
        try:
            from app.application.notifications import (
                notify_high_risk_sync_changes,
            )

            notify_high_risk_sync_changes(
                db,
                business_id=job.business_id,
                correlation_id=job.correlation_id,
                product_ids=high_risk_products,
                source_id=source.source_id,
            )
        except Exception:
            pass

    return {
        "auto_updated": auto_updated,
        "needs_review": needs_review,
        "skipped": skipped,
    }


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
    - Hybrid auto-publish: LOW/MEDIUM auto-edit, HIGH/CRITICAL notify
    """
    db = get_session_factory()()
    try:
        job = db.get(models.SyncJob, uuid.UUID(sync_id))
        if job is None:
            return {"sync_id": sync_id, "status": "NOT_FOUND"}

        # Entitlement gate BEFORE consuming an attempt
        try:
            entitlements.require_entitlement_unchecked(
                db, business_id=job.business_id
            )
        except entitlements.EntitlementDenied as exc:
            sync_jobs.fail_final(db, job, error=str(exc))
            db.commit()
            return {
                "sync_id": sync_id,
                "status": enums.SyncJobStatus.FAILED_FINAL.value,
            }

        # Now claim the job
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

        sync_jobs.heartbeat(job)
        db.flush()

        outcome = import_pipeline.run_import(
            db,
            source=source,
            business_id=job.business_id,
            actor_id=job.requested_by,
            correlation_id=job.correlation_id,
        )

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

        # Hybrid auto-publish (only if automatic_sync_enabled)
        auto_result = {"auto_updated": 0, "needs_review": 0, "skipped": 0}
        if source.automatic_sync_enabled:
            try:
                auto_result = _auto_update_changed_products(
                    db, job, source, outcome
                )
            except Exception:
                # Auto-publish must never fail the sync job itself
                auto_result = {
                    "auto_updated": 0,
                    "needs_review": 0,
                    "skipped": 0,
                    "error": "auto_update_failed",
                }

        db.commit()
        return {
            "sync_id": sync_id,
            "status": job.status,
            "counts": job.counts,
            "auto_publish": auto_result,
        }
    except entitlements.EntitlementDenied as exc:
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
    except Exception as exc:  # noqa: BLE001
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
        return {
            "sync_id": sync_id,
            "status": "RETRY_SCHEDULED",
            "error": type(exc).__name__,
        }
    finally:
        db.close()
