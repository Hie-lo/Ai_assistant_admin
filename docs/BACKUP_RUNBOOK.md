# Backup & Restore Runbook — دستیار هوشمند کسب‌وکارهای مجازی

Status: Phase 10 — Operational
Date: 2026-09-16

Per OBSERVABILITY_BACKUP_DR_SPECIFICATION_V1 and Gate H.

## 1. Design

- Encrypted periodic full backup + local rotation + off-site copy
- Backup manifest: timestamp, app_version, schema_version, type, checksum, encryption, retention
- Local rotation: keep last 10 daily backups (configurable)
- Off-site: optional S3 or Telegram encrypted copy — never sole source
- Restore tests mandatory — file creation != successful backup

## 2. Backup Creation

### Automatic (Celery Beat)

- Task: `backup.create_daily` runs daily via beat (86400s)
- Creates encrypted dump in `backups/` with manifest JSON
- Updates metrics: `backup_created_total`, `backup_last_size_bytes`

### Manual

```bash
python scripts/backup.py --type full
python scripts/backup.py --list
python scripts/backup.py --verify backup_20260916_120000.enc
```

Environment required:
- DATABASE_URL (postgresql+psycopg://...)
- SECRET_KEY (for encryption key derivation)

Encryption:
- Fernet (AES-128 CBC + HMAC) via cryptography library
- Key derived from SECRET_KEY via SHA256 -> base64 urlsafe
- If cryptography missing, fallback to unencrypted (dev only, logs warning)

## 3. Manifest Example

```json
{
  "timestamp": "20260916_120000",
  "app_version": "0.1.0",
  "schema_version": "20260916_f0a1b2c3d4e5",
  "backup_type": "full",
  "checksum": "sha256:...",
  "encryption": "fernet-aes128",
  "retention_class": "daily",
  "file_name": "backup_20260916_120000.enc",
  "size_bytes": 123456,
  "required_components": ["database", "media", "config"]
}
```

## 4. Restore

### Dry-run (verify)

```bash
python scripts/restore.py --file backup_20260916_120000.enc --dry-run
```

### Full restore

```bash
# On new server:
1. provision compatible runtime (Python 3.13, PostgreSQL 18, Redis 7)
2. cp .env.example .env and fill secrets
3. python scripts/restore.py --file backup_20260916_120000.enc
4. alembic upgrade head
5. python -m pytest tests/integration -k smoke
6. Verify /healthz, /readyz, /metrics
7. Reconcile UNKNOWN_REMOTE_STATE publications
8. Enable traffic (nginx) and jobs (celery beat)
```

Steps per spec section 10:

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

### Decrypt for inspection

```bash
python scripts/restore.py --file backup_20260916_120000.enc --decrypt-only /tmp/decrypted.sql
```

## 5. Off-site Copy

V1 supports manual off-site:

- S3: `aws s3 cp backups/backup_*.enc s3://bucket/ai-assistant-backups/`
- Telegram: optional encrypted copy via bot (notification + file if <50MB)
- Second VPS: rsync with SSH

Never rely solely on Telegram. Off-site destination pending server details (Gate H).

## 6. Monitoring

- Metric: `backup_created_total` counter
- Metric: `backup_last_size_bytes` gauge
- Alert: if no backup in 26h -> critical
- Health: /api/v1/admin/backups lists backups, /verify/{file} verifies

## 7. Rotation

- Local: keep last 10 (configurable in service._rotate_backups)
- Off-site: keep 30 daily + 12 weekly (operator policy)
- Media: product media is URL-based in V1 (no local files), so only DB backup needed

## 8. Security

- Backups encrypted at rest (Fernet)
- Access controlled (backups/ git-ignored, 700 perms in prod)
- Secret rotation: if compromise suspected, rotate SECRET_KEY and re-encrypt
- Never log secret, token, or full backup content

## 9. Restore Test Schedule

- Weekly: automated dry-run verification on staging
- Monthly: full restore to clean environment
- Before production release: mandatory restore pass (Gate I)

## 10. Failure Scenarios

- pg_dump fails: backup file contains error note, manifest still written, alert
- Disk full: rotation runs before creation, fails closed with clear message
- Decrypt fails: verify returns ok=False, restore aborts
- Schema mismatch: alembic upgrade head after restore handles it
- Media missing: V1 media is URLs, so no local media restore needed; future S3 needs separate step
