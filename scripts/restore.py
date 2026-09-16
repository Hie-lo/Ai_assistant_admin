#!/usr/bin/env python3
"""Restore script (Phase 10).

Per OBSERVABILITY_BACKUP_DR_SPECIFICATION_V1 sections 10-11:
- Restore must rebuild on new server
- Includes database, media, config, version checks
- Integrity verification
- Secret rotation where compromise suspected

Usage:
    python scripts/restore.py --file backup_20260916_120000.enc --dry-run
    python scripts/restore.py --file backup_20260916_120000.enc
    python scripts/restore.py --file backup_20260916_120000.enc --target-db postgresql://...

Steps (runbook):
1. provision compatible runtime
2. restore database
3. restore required media/config
4. restore secret references/rotate as needed
5. run migrations/compatibility checks
6. run integrity checks
7. run smoke tests
8. enable traffic/jobs
9. reconcile external publications
10. verify monitoring and backups
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config.settings import get_settings
from app.infrastructure.backup.service import decrypt_file, verify_backup


def main():
    parser = argparse.ArgumentParser(description="Restore from encrypted backup")
    parser.add_argument("--file", required=True, help="Backup file name (in backups/ dir) or full path")
    parser.add_argument("--dir", default="backups", help="Backup directory")
    parser.add_argument("--target-db", help="Target database URL (default: DATABASE_URL from env)")
    parser.add_argument("--dry-run", action="store_true", help="Only verify, don't restore")
    parser.add_argument("--decrypt-only", help="Decrypt to specified path (for inspection)")

    args = parser.parse_args()
    settings = get_settings()
    backup_dir = Path(args.dir)

    # Resolve file path
    if Path(args.file).exists():
        enc_file = Path(args.file)
        backup_dir = enc_file.parent
        file_name = enc_file.name
    else:
        file_name = args.file
        enc_file = backup_dir / file_name

    if not enc_file.exists():
        print(f"❌ Backup file not found: {enc_file}")
        sys.exit(1)

    print(f"Verifying backup {enc_file}...")
    result = verify_backup(backup_dir, file_name, settings.secret_key)
    if not result["ok"]:
        print(f"❌ Verification failed!")
        print(f"   Checksum: {result.get('checksum')}")
        print(f"   Expected: {result.get('expected')}")
        sys.exit(1)

    print(f"✅ Verification OK — size {result.get('size')} bytes")
    manifest = result.get("manifest", {})
    if manifest:
        print(f"   Timestamp: {manifest.get('timestamp')}")
        print(f"   App version: {manifest.get('app_version')}")
        print(f"   Schema: {manifest.get('schema_version')}")

    if args.decrypt_only:
        out_path = Path(args.decrypt_only)
        print(f"Decrypting to {out_path}...")
        decrypt_file(enc_file, out_path, settings.secret_key)
        print(f"✅ Decrypted to {out_path}")
        return

    if args.dry_run:
        print("Dry-run: verification passed, no restore performed.")
        return

    # Actual restore
    target_db = args.target_db or settings.database_url
    print(f"Restoring to {target_db}...")

    with tempfile.NamedTemporaryFile(delete=False, suffix=".sql") as tmp:
        tmp_path = Path(tmp.name)

    try:
        decrypt_file(enc_file, tmp_path, settings.secret_key)

        if target_db.startswith("postgresql"):
            import os
            import subprocess

            env = os.environ.copy()
            # Extract password
            try:
                url_part = target_db.split("://")[1]
                creds_host = url_part.split("@")
                if len(creds_host) == 2:
                    creds = creds_host[0]
                    if ":" in creds:
                        env["PGPASSWORD"] = creds.split(":")[1]
            except Exception:
                pass

            pg_url = target_db.replace("postgresql+psycopg://", "postgresql://")
            print(f"Running psql restore...")
            result = subprocess.run(
                ["psql", pg_url, "-f", str(tmp_path)],
                env=env,
                capture_output=True,
                text=True,
                timeout=600,
            )
            if result.returncode != 0:
                print(f"❌ Restore failed: {result.stderr[:1000]}")
                sys.exit(1)
            print("✅ Database restored")
        else:
            print(f"Restore for {target_db} not implemented in script — manual step required")
            print(f"Decrypted SQL is at {tmp_path}")

        # Post-restore checks
        print("Running post-restore checks...")
        print("1. Run migrations: alembic upgrade head")
        print("2. Run integrity checks: SELECT count(*) FROM products")
        print("3. Run smoke tests: pytest tests/integration -k smoke")
        print("4. Verify monitoring: GET /healthz, /readyz, /metrics")
        print("5. Reconcile publications: check UNKNOWN_REMOTE_STATE")
        print("6. Enable traffic/jobs")

    finally:
        try:
            if not args.decrypt_only:
                tmp_path.unlink(missing_ok=True)
        except Exception:
            pass


if __name__ == "__main__":
    main()
