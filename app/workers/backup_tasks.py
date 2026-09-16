"""Backup tasks (Phase 10).

Per OBSERVABILITY_BACKUP_DR_SPECIFICATION_V1:
- Encrypted periodic backups + local rotation + off-site copy
- Backup manifest
- Restore tests mandatory (not just file creation)
"""

from __future__ import annotations

from pathlib import Path

from celery import shared_task

from app.config.settings import get_settings
from app.infrastructure.backup.service import create_backup


@shared_task(name="backup.create_daily", bind=True, max_retries=2)
def create_daily_backup(self) -> dict:
    settings = get_settings()
    backup_dir = Path("backups")
    try:
        manifest = create_backup(
            database_url=settings.database_url,
            backup_dir=backup_dir,
            secret_key=settings.secret_key,
            backup_type="full",
            retention_class="daily",
        )
        return {
            "file": manifest.file_name,
            "checksum": manifest.checksum,
            "size": manifest.size_bytes,
            "timestamp": manifest.timestamp,
        }
    except Exception as exc:
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries)) from exc
