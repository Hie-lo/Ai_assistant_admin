"""Durable in-app notification use cases."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import enums
from app.infrastructure.db import models


def notify_sync_failure(
    db: Session,
    *,
    job: models.SyncJob,
    kind: enums.NotificationKind,
    error: str,
) -> list[models.Notification]:
    """Notify active Owner/Admin members once for a terminal sync outcome."""
    memberships = db.scalars(
        select(models.Membership).where(
            models.Membership.business_id == job.business_id,
            models.Membership.status
            == enums.MembershipStatus.ACTIVE.value,
            models.Membership.role.in_(
                [
                    enums.MembershipRole.OWNER.value,
                    enums.MembershipRole.ADMIN.value,
                ]
            ),
        )
    ).all()
    title = (
        "همگام‌سازی با شکست نهایی"
        if kind is enums.NotificationKind.SYNC_RETRY_EXHAUSTED
        else "همگام‌سازی نیازمند بررسی است"
    )
    body = f"همگام‌سازی پس از {job.attempt_count} تلاش به نتیجه نرسید."
    payload = {
        "sync_id": str(job.sync_id),
        "source_id": str(job.source_id),
        "attempt_count": job.attempt_count,
        "error": error[:1000],
        "counts": job.counts or {},
        "row_errors": job.row_errors or [],
    }
    created: list[models.Notification] = []
    for membership in memberships:
        duplicate = db.scalar(
            select(models.Notification).where(
                models.Notification.recipient_user_id
                == membership.user_id,
                models.Notification.correlation_id == job.correlation_id,
                models.Notification.kind == kind.value,
            )
        )
        if duplicate is not None:
            continue
        notification = models.Notification(
            business_id=job.business_id,
            recipient_user_id=membership.user_id,
            kind=kind.value,
            status=enums.NotificationStatus.UNREAD.value,
            title=title,
            body=body,
            data=payload,
            correlation_id=job.correlation_id,
        )
        db.add(notification)
        created.append(notification)
    db.flush()
    return created


def notify_high_risk_sync_changes(
    db: Session,
    *,
    business_id: uuid.UUID,
    correlation_id: str,
    product_ids: list[uuid.UUID],
    source_id: uuid.UUID,
) -> list[models.Notification]:
    """Notify Owner/Admin that high-risk changes need manual review.

    Hybrid mode: LOW/MEDIUM auto-edit, HIGH/CRITICAL + REPOST needs review.
    """
    memberships = db.scalars(
        select(models.Membership).where(
            models.Membership.business_id == business_id,
            models.Membership.status
            == enums.MembershipStatus.ACTIVE.value,
            models.Membership.role.in_(
                [
                    enums.MembershipRole.OWNER.value,
                    enums.MembershipRole.ADMIN.value,
                ]
            ),
        )
    ).all()

    title = "تغییرات پرریسک نیازمند بررسی"
    body = (
        f"{len(product_ids)} محصول با تغییرات پرریسک یا نیازمند "
        "بازانتشار شناسایی شد. لطفاً از پنل وب بررسی کنید."
    )
    payload = {
        "source_id": str(source_id),
        "product_ids": [str(pid) for pid in product_ids],
        "count": len(product_ids),
    }

    created: list[models.Notification] = []
    for membership in memberships:
        # Avoid duplicate notification for same correlation_id
        duplicate = db.scalar(
            select(models.Notification).where(
                models.Notification.recipient_user_id
                == membership.user_id,
                models.Notification.correlation_id == correlation_id,
                models.Notification.kind
                == enums.NotificationKind.SYNC_RECOVERY_REQUIRED.value,
            )
        )
        if duplicate is not None:
            continue
        notification = models.Notification(
            business_id=business_id,
            recipient_user_id=membership.user_id,
            kind=enums.NotificationKind.SYNC_RECOVERY_REQUIRED.value,
            status=enums.NotificationStatus.UNREAD.value,
            title=title,
            body=body,
            data=payload,
            correlation_id=correlation_id,
        )
        db.add(notification)
        created.append(notification)
    db.flush()
    return created


def mark_read(
    db: Session, *, notification_id: uuid.UUID, user_id: uuid.UUID
) -> bool:
    notification = db.get(models.Notification, notification_id)
    if notification is None or notification.recipient_user_id != user_id:
        return False
    notification.status = enums.NotificationStatus.READ.value
    notification.read_at = datetime.now(UTC)
    return True
