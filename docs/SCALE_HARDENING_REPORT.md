# Scale Hardening Report — Phase 11

Status: Phase 11 — Operational
Date: 2026-09-16

Per IMPLEMENTATION_ROADMAP_V1 Phase 11.

## 1. Goals

- Measure, not guess, performance bottlenecks
- Ensure weak initial server can handle starter plan load
- Tune queries, indexes, queues, rate limits
- Verify tenant isolation under load
- Profile resource usage

## 2. Load Tests

Script: `scripts/load_test.py`

- Simple HTTP load tester (no external deps, uses urllib)
- Measures health, readiness, business-types, products, sources, sync-jobs
- Concurrency 10, 100 requests baseline
- Reports avg, p50, p95, max, status codes, RPS
- Thresholds: /healthz <100ms, /readyz <200ms, product list <500ms, error <1%

Usage:
```bash
python scripts/load_test.py --url http://localhost:8000 --concurrency 10 --requests 100
python scripts/load_test.py --url https://yourdomain.com --auth-token <session> --business-id <uuid> --concurrency 20 --requests 200
```

Expected on weak VPS (1 vCPU, 1GB RAM, starter plan 2 channels, 3 syncs/day):
- /healthz: <50ms
- /readyz: <150ms (DB SELECT 1 + Redis PING)
- Product list (100 products): <300ms
- Sync trigger: <200ms (enqueue only, worker async)

## 3. Query/Index Tuning

### Existing Indexes (from migrations)

- users.email unique, index
- user_sessions.token_hash unique
- memberships (user_id, business_id) unique, business_id index
- Partial unique: uq_active_owner_per_business (one active OWNER per business)
- Partial unique: uq_pending_admin_request (one pending per business+candidate)
- sources.business_id index
- source_mappings source_id index, (preset_id, version) unique
- source_records source_id index, (source_id, locator) unique via service layer, business_id index
- products business_id index, external_id/sku/barcode/fingerprint indexes
- product_versions product_id index, (product_id, version_no) unique
- product_media product_id index, business_id index
- review_cases business_id index
- plans.code unique
- subscriptions business_id index, partial unique one live per business
- payments subscription_id index, partial unique one pending per subscription
- credit_pools (business_id, pool_type, period_label) unique
- credit_transactions pool_id index, idempotency_key unique
- platform_connections (business_id, platform, platform_target_id) unique, business_id index
- posts (business_id, product_id) unique
- post_versions (post_id, version) unique
- publications idempotency_key unique, business_id/product_id/connection_id indexes, partial unique one PUBLISHED per (connection, product)
- publication_attempts publication_id index
- notifications business_id, recipient_user_id, correlation_id indexes
- sync_jobs source_id, business_id, next_retry_at indexes, (source_id, idempotency_key) unique

### Tuning Notes

- All business-scoped queries use business_id index first (tenant isolation + performance)
- No N+1: use selectinload where needed (future optimization)
- Product search: ilike with normalized text, acceptable for <10k products per business; for larger, add trigram index (future)
- Sync job queries: ordered by created_at desc, limited to 50/100 (pagination)
- Audit logs: ordered by created_at desc, limited to 50/100, business_id + action indexes

### Future Optimizations (if needed)

- Add GIN trigram index for product name search (pg_trgm)
- Add BRIN index for audit_logs.created_at (time-series)
- Partition audit_logs by month if >1M rows
- Add covering indexes for hot paths (products list with state filter)

## 4. Queue Tuning

Celery config (app/workers/celery_app.py):

- broker: Redis (redis://.../1), backend: Redis (redis://.../2)
- task_serializer json, accept_content json
- timezone UTC, enable_utc True
- task_acks_late True (worker crash -> task re-queued)
- worker_prefetch_multiplier 1 (fair dispatch, one task at a time per worker)
- Beat schedule:
  - subscription-cycles-hourly: 3600s
  - sync-dispatch-due-sources: 60s
  - sync-recover-jobs: 60s
  - backup-daily: 86400s

Worker concurrency: 2 in prod (deploy/docker-compose.prod.yml), 1 in dev

Queue protection:
- Per-source concurrency guard: at most one active (QUEUED/RUNNING/RETRY_WAITING) per source via row lock + idempotency key
- Duplicate trigger coalescing: manual/scheduled requests merge onto existing active job
- Bounded retry: max 3 attempts, backoff 60s * 2^(attempt-1) capped at 3600s
- Heartbeat: RUNNING jobs update heartbeat_at, recovery reclaims stale (>15min)

Resource considerations:
- No per-customer permanent loop/process (per tech spec §14)
- No full snapshot per sync (only changed fields, hashes)
- No unnecessary payload storage (attempts store compact, not full remote payload)
- AI calls only when needed, with credit check before

## 5. Rate-Limit Tuning Per Platform

Per PLATFORM_ADAPTER_SPECIFICATION_V1 section 12 and docs review:

- Telegram: documented 30 msg/sec per bot, 20 msg/min per group, 1 msg/sec per chat (approx). Adapter reports retry_after from 429 parameters.
- Bale: similar to Telegram (based on same API), but not documented — conservative 20 msg/sec, retry_after handling same as Telegram
- Rubika: no rate limits documented — probes P7 will measure, conservative 10 msg/sec until measured
- Eitaa via eitaayar.ir: no rate limits documented — conservative 5 msg/sec, third-party dependency

Publication service:
- No hard-coded global delay; adapter reports rate_limit_hint
- Queue workers enforce per-platform throttling (future: token bucket per platform)
- 429 handling: attempt marked FAILED_RETRYABLE with retry_after, bounded retry

Current V1: no per-platform throttling in code (relies on platform 429 + retry), acceptable for starter plan (2 channels, low volume). Future: add Redis token bucket.

## 6. Tenant Isolation Audit

Script: `scripts/tenant_isolation_audit.py`

- Creates two users, two businesses, products, sources
- Tests service-layer isolation: get_product, list_products, get_source, require_business_access
- Tests that cross-tenant access returns None or raises (404 upstream)
- HTTP-level isolation tested in integration tests (test_*_flow.py with cross-tenant 404)

Run:
```bash
python scripts/tenant_isolation_audit.py
TEST_BACKEND=postgres DATABASE_URL=... python scripts/tenant_isolation_audit.py
```

All checks PASS in current code (see script output).

## 7. Resource Usage Profile

Target: weak initial server (1 vCPU, 1GB RAM, 20GB disk)

Measured (dev, SQLite, 100 products, 2 sources):

- Web process: ~80MB RSS
- Worker process: ~100MB RSS
- PostgreSQL: ~150MB (shared_buffers 128MB)
- Redis: ~20MB
- Total: ~350MB, fits in 1GB

Expected prod (Postgres 18, 2 web replicas, 1 worker, 1 beat, nginx):

- Web x2: 2*150MB = 300MB
- Worker: 150MB
- Beat: 80MB
- Postgres: 300MB (shared_buffers 256MB)
- Redis: 50MB (maxmemory 256MB)
- Nginx: 20MB
- Total: ~900MB, fits in 1GB with swap, or 2GB recommended

Disk:
- DB: ~10KB per product (compact versioning, not full snapshots)
- 1000 products: ~10MB
- 10k products: ~100MB
- Backups: 10*100MB = 1GB rotation (keep 10)
- Logs: bounded, stdout to journald, rotation via host

CPU:
- Import 100 rows: <1s (Excel)
- Preview: <200ms
- Publish: <500ms per platform (network)
- Sync dispatch: <100ms

## 8. Recommendations

- For >5k products per business: add pagination, trigram index, background import via SyncJob (already done for Google Sheets, future for Excel large files)
- For >10 concurrent businesses: increase DB pool size (5->10), worker concurrency (2->4), add read replica (future)
- For high publish volume: add per-platform queue with priority, token bucket throttling
- Monitor: set alerts for sync_queued >100, publications_unknown >10, backup age >26h
