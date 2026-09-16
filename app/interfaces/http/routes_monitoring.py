"""Monitoring and observability routes (Phase 10).

Per OBSERVABILITY_BACKUP_DR_SPECIFICATION_V1:
- Structured operation log with correlation_id (already in app.py middleware)
- Health checks: /healthz (liveness) + /readyz (readiness + DB)
- Metrics: /metrics (JSON for V1, Prometheus-compatible later)
- Audit log viewer (admin)
- Backup status (admin)

Security: metrics endpoint requires super_admin in prod, open in dev for
debugging; audit logs are business-scoped and permission-gated.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from app.config.settings import get_settings
from app.domain.permissions import BUSINESS_VIEW
from app.infrastructure.db import models
from app.infrastructure.monitoring.metrics import get_registry
from app.interfaces.http.deps import Db, SuperAdmin, require_business_permission

router = APIRouter(tags=["monitoring"])


@router.get("/metrics")
def get_metrics(db: Db, _admin: SuperAdmin):
    """Return current metrics snapshot.

    Protected: platform operator only (super_admin).
    """
    registry = get_registry()
    snapshot = registry.snapshot()

    # Add DB stats
    try:
        from sqlalchemy import func

        product_count = db.scalar(select(func.count()).select_from(models.Product)) or 0
        business_count = db.scalar(select(func.count()).select_from(models.Business)) or 0
        sync_queued = db.scalar(
            select(func.count())
            .select_from(models.SyncJob)
            .where(models.SyncJob.status == "QUEUED")
        ) or 0
        sync_running = db.scalar(
            select(func.count())
            .select_from(models.SyncJob)
            .where(models.SyncJob.status == "RUNNING")
        ) or 0
        pub_unknown = db.scalar(
            select(func.count())
            .select_from(models.Publication)
            .where(models.Publication.status == "UNKNOWN_REMOTE_STATE")
        ) or 0

        snapshot["gauges"]["db_products_total"] = product_count
        snapshot["gauges"]["db_businesses_total"] = business_count
        snapshot["gauges"]["sync_queued"] = sync_queued
        snapshot["gauges"]["sync_running"] = sync_running
        snapshot["gauges"]["publications_unknown"] = pub_unknown
    except Exception:
        pass

    return snapshot


@router.get("/metrics/prometheus")
def get_metrics_prometheus(db: Db):
    """Prometheus text format (optional, for future integration)."""
    registry = get_registry()
    snap = registry.snapshot()
    lines = []
    for key, value in snap["counters"].items():
        lines.append(f"# TYPE {key.split('{')[0]} counter")
        lines.append(f"{key} {value}")
    for key, value in snap["gauges"].items():
        lines.append(f"# TYPE {key.split('{')[0]} gauge")
        lines.append(f"{key} {value}")
    return "\n".join(lines)


# --- Audit log viewer (business-scoped) --------------------------------------


@router.get("/api/v1/businesses/{business_id}/audit-logs")
def list_audit_logs(
    business_id: uuid.UUID,
    db: Db,
    _scope: Annotated[object, Depends(require_business_permission(BUSINESS_VIEW))],
    action: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
):
    stmt = select(models.AuditLog).where(models.AuditLog.business_id == business_id)
    if action:
        stmt = stmt.where(models.AuditLog.action == action)
    stmt = stmt.order_by(models.AuditLog.created_at.desc()).limit(limit)
    logs = db.scalars(stmt).all()
    return [
        {
            "log_id": str(entry.log_id),
            "actor_user_id": str(entry.actor_user_id)
            if entry.actor_user_id
            else None,
            "action": entry.action,
            "target_type": entry.target_type,
            "target_id": entry.target_id,
            "outcome": entry.outcome,
            "correlation_id": entry.correlation_id,
            "meta_data": entry.meta_data,
            "created_at": entry.created_at.isoformat()
            if entry.created_at
            else None,
        }
        for entry in logs
    ]


# --- Backup status (admin) ---------------------------------------------------


@router.get("/api/v1/admin/backups")
def list_backups_admin(db: Db, _admin: SuperAdmin):
    from pathlib import Path

    from app.infrastructure.backup.service import list_backups

    backup_dir = Path("backups")
    return list_backups(backup_dir)


@router.get("/api/v1/admin/backups/verify/{file_name}")
def verify_backup_admin(file_name: str, db: Db, _admin: SuperAdmin):
    from pathlib import Path

    from app.infrastructure.backup.service import verify_backup

    settings = get_settings()
    backup_dir = Path("backups")
    return verify_backup(backup_dir, file_name, settings.secret_key)
