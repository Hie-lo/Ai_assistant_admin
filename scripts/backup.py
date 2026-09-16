#!/usr/bin/env python3
"""Encrypted backup script (Phase 10).

Per OBSERVABILITY_BACKUP_DR_SPECIFICATION_V1 sections 7-11:
- Encrypted periodic full backup + compact incremental
- Local rotation controls disk use
- Off-site copy is optional
- Backup manifest with timestamp, version, checksum, etc.
- Restore tests mandatory

Usage:
    python scripts/backup.py --type full
    python scripts/backup.py --type full --offsite s3://bucket/path
    python scripts/backup.py --verify backup_20260916_120000.enc

Environment:
    DATABASE_URL, SECRET_KEY must be set (via .env or env vars)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config.settings import get_settings
from app.infrastructure.backup.service import create_backup, list_backups, verify_backup


def main():
    parser = argparse.ArgumentParser(description="Create encrypted backup")
    parser.add_argument("--type", default="full", choices=["full", "incremental"], help="Backup type")
    parser.add_argument("--dir", default="backups", help="Backup directory")
    parser.add_argument("--verify", help="Verify a backup file")
    parser.add_argument("--list", action="store_true", help="List backups")
    parser.add_argument("--offsite", help="Off-site destination (e.g. s3://bucket/path or telegram)")

    args = parser.parse_args()

    settings = get_settings()
    backup_dir = Path(args.dir)

    if args.list:
        backups = list_backups(backup_dir)
        if not backups:
            print("No backups found")
            return
        for b in backups:
            print(f"{b['timestamp']} — {b['file_name']} — {b['size_bytes']} bytes — {b['backup_type']} — {b['checksum'][:16]}...")
        return

    if args.verify:
        result = verify_backup(backup_dir, args.verify, settings.secret_key)
        print(f"Verify {args.verify}:")
        print(f"  OK: {result['ok']}")
        print(f"  Checksum: {result.get('checksum')}")
        print(f"  Expected: {result.get('expected')}")
        print(f"  Size: {result.get('size')}")
        if not result["ok"]:
            sys.exit(1)
        return

    print(f"Creating {args.type} backup in {backup_dir}...")
    manifest = create_backup(
        database_url=settings.database_url,
        backup_dir=backup_dir,
        secret_key=settings.secret_key,
        backup_type=args.type,
    )
    print(f"✅ Backup created: {manifest.file_name}")
    print(f"   Timestamp: {manifest.timestamp}")
    print(f"   App version: {manifest.app_version}")
    print(f"   Schema version: {manifest.schema_version}")
    print(f"   Checksum: {manifest.checksum}")
    print(f"   Size: {manifest.size_bytes} bytes")
    print(f"   Encryption: {manifest.encryption}")

    if args.offsite:
        print(f"Off-site copy to {args.offsite} is configured as optional (not implemented in V1 script).")
        print("For production, configure S3 or Telegram copy via deploy/runbook.")

    # Update metrics
    try:
        from app.infrastructure.monitoring.metrics import inc_counter, set_gauge

        inc_counter("backup_created_total", 1, type=args.type)
        set_gauge("backup_last_size_bytes", manifest.size_bytes)
    except Exception:
        pass


if __name__ == "__main__":
    main()
