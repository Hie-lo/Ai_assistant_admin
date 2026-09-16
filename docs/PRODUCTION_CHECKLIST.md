# Production Readiness Checklist — دستیار هوشمند کسب‌وکارهای مجازی

Status: Phase 12 — Final Review
Date: 2026-09-16

Per FINAL_APPROVAL_GATES_V1 Gate I and 00_START_HERE.md section 20.

## Functional Coverage

- [x] Phase 0 — Approval & baseline: 22 docs, CI, Alembic, Docker, structural tests
- [x] Phase 1 — Auth/User/Business/Membership/RBAC: email+password, Argon2id, sessions, OWNER/ADMIN, invite codes, link codes, audit
- [x] Phase 2 — Subscription/Payment/Entitlement: plan catalog, lifecycle PENDING/ACTIVE/GRACE/EXPIRED/SUSPENDED/CANCELLED/REFUNDED, manual payments, credit ledger, entitlement engine
- [x] Phase 3 — Source/Product core: Excel + Google Sheets adapters, versioned mapping, identity resolution (external_id->SKU->barcode->fingerprint), product versions, media, review cases, import pipeline
- [x] Phase 4 — Content/Presets/AI: versioned presets per business type + per-product presets (top-plan), AI definitions versioned, generation with credit consumption, approval flow, preview with priority trimming
- [x] Phase 5 — Telegram certification + publication: adapter, capability matrix (4096/1024/10), 17-state machine, idempotency, attempts, repost order new->verify->delete old, timeout=UNKNOWN, reconciliation
- [x] Phase 6 — Bale certification + publication: shared org bot, markdown escaping, single photo 4096 caption, album <=10 with 1024 caption, 48h lingering policy, live certification run #6 PASS
- [x] Phase 7 — Eitaa/Rubika: DEFERRED per owner decision, contract review committed (docs/PHASE7_EITAA_RUBIKA_CONTRACT_REVIEW.md), Rubika official REST API reviewed, Eitaa no public API (third-party eitaayar.ir captured)
- [x] Phase 8 — Sync engine: durable SyncJob, queue workers, bounded retry/backoff (max 3), idempotency, per-source concurrency guard, manual/scheduled coalescing, crash recovery, final failure notification, Excel=Manual only, Google Sheets=Manual/Scheduled/Automatic
- [x] Phase 9 — Interfaces: Web panel (Jinja2+HTMX, base/login/register/dashboard/business/products), Telegram bot (link code, /businesses, /products, /sync, /notifications), Bale bot (same flow, markdown escaping), unified permission layer
- [x] Phase 10 — Monitoring/Backup/DR: structured logs with correlation_id, metrics (in-process registry, /metrics, /metrics/prometheus), health/readiness, audit log viewer, encrypted backups with manifest + rotation + verify, restore script, runbooks
- [x] Phase 11 — Scale hardening: load test script, query/index tuning (partial unique indexes, business_id indexes), queue tuning (acks_late, prefetch 1, concurrency 2), rate-limit tuning per platform, tenant isolation audit
- [x] Phase 12 — Production readiness: security review, failure matrix, backup restore, platform certification, rollback rehearsal, release checklist (this doc)

## Failure Coverage

Per TEST_STRATEGY_V1 and domain specs failure matrices:

- [x] Row reorder/insert/delete/recreate -> identity continuity
- [x] No ID / duplicated ID / reused ID -> review case
- [x] Changed SKU/barcode -> CRITICAL + REVIEW_REQUIRED
- [x] Column rename/add/remove -> mapping versioning, inert unknown columns
- [x] Permission revoked / source unavailable / partial read -> incomplete read, no missing inference, blocked with diagnostics
- [x] Mass disappearance (>50% baseline) -> MASS_MISSING_BLOCKED review case, no destructive action
- [x] Suspicious change (price jump >50%, mass HIGH-risk >=5) -> quarantine remaining rows
- [x] Invalid rows -> BLANK vs INVALID, exact row+column diagnostics, SOURCE_INVALID reversible
- [x] Publish success / duplicate job / duplicate worker -> effectively-once via idempotency key + unique index
- [x] Timeout after likely remote success -> UNKNOWN_REMOTE_STATE, no blind retry, reconciliation
- [x] Remote success + local DB failure -> attempt record, recovery via durable state
- [x] Remote manual delete -> REMOTE_DELETED, post archived, no auto-repost
- [x] Remote manual edit -> remote_modified flagged, never overwrite
- [x] Permission loss / disconnect -> SUSPENDS publications, resume via reconciliation (not bulk republish)
- [x] Server restart mid-batch / worker crash -> SyncJob recovery via heartbeat, retry_or_exhaust
- [x] Subscription expiry while queued -> re-evaluate before external side effect (entitlement gate in worker)
- [x] Admin removed while job queued -> session revocation, queued work re-evaluated (permission check before publish)
- [x] Cross-tenant access -> 404

## Security

- [x] Tenant isolation: business-scoped queries, 404 for cross-tenant
- [x] Authentication: Argon2id, sessions hashed, lockout, rate limit
- [x] Authorization: server-side, permission matrix, owner-only stripping, audit
- [x] Platform certification: Telegram + Bale live certified (Bale run #6)
- [x] Data integrity: durable state, not cache-only, fingerprints, versioning
- [x] Idempotency: deterministic keys, unique indexes, coalescing
- [x] Concurrency: row locks, partial unique indexes, per-source guard
- [x] Backup: encrypted, rotation, manifest, verify, restore script
- [x] Restore: dry-run + full restore tested (manual step, runbook)
- [x] Monitoring: structured logs, metrics, health/readiness, audit
- [x] Performance/resource: no per-customer loop, compact state, bounded history, no full snapshot per sync

## Platform Certification

- [x] Telegram: text 4096, caption 1024, album 10, edit/delete/inspect supported, live tested
- [x] Bale: text 4096, single photo caption 4096, album item caption 1024, album <=10 (conservative, live measured >=10), edit/delete supported, inspect_remote=False (no lookup), markdown escaping live-verified, 48h lingering policy, certification run #6 PASS (19/19 cleanup)
- [ ] Eitaa: DEFERRED, contract review done, no public official API, third-party eitaayar.ir = send-only (no edit/delete/album), bonuses scheduled send + pin
- [ ] Rubika: DEFERRED, official REST API reviewed, 2-step media upload (requestSendFile->upload->file_id->sendFile), no album/caption-edit, no error model, limits unknown -> probes P0-P7 defined

## Data Integrity

- [x] Product ID immutable, row number never identity
- [x] Missing source != deletion (MISSING_FROM_SOURCE, non-destructive)
- [x] Ambiguous identity never auto-merge (review case)
- [x] AI never modifies canonical facts
- [x] Historical Preset/AI/Mapping versions immutable
- [x] Critical state durable (PostgreSQL), cache disposable (Redis)

## Test Coverage

- Unit: ~200 tests (domain, adapters, policies, content, identity, etc.)
- Integration: ~150 tests (auth, RBAC, billing, import, content, publication, bale flow, etc.)
- Contract: platform adapter wire-format tests
- E2E: critical journeys (register->business->source->mapping->import->product->preset->preview->connection->publish)
- Failure: sync jobs, retry, coalescing, recovery, blank/invalid rows, mass missing, suspicious change, etc.
- Security: cross-tenant, privilege escalation, revocation, SSRF, malicious input (partial)
- Load: scripts/load_test.py (basic)

Run:
```bash
make test  # unit
make test-integration  # integration (needs postgres+redis)
pytest tests/failure -v
python scripts/load_test.py --url http://localhost:8000 --concurrency 10 --requests 100
```

## Documentation

- [x] 00_START_HERE.md (bootstrap contract)
- [x] 22 design docs in docs/ (MASTER, PRODUCT_V2, POST_PUBLICATION, RBAC, SOURCE_SYNC, PLATFORM_ADAPTER, AI_CONFIGURATION, SUBSCRIPTION, SECURITY, OBSERVABILITY, TECHNOLOGY, TEST_STRATEGY, ROADMAP, etc.)
- [x] PROJECT_LOG.md (phase logs)
- [x] PROJECT_CHANGELOG_AND_DECISIONS.md (decisions)
- [x] BACKUP_RUNBOOK.md
- [x] MONITORING_RUNBOOK.md
- [x] SECURITY_REVIEW.md
- [x] PRODUCTION_CHECKLIST.md (this file)
- [x] deploy/nginx.conf, docker-compose.prod.yml
- [x] scripts/backup.py, restore.py, certify_platform.py, load_test.py

## Known Risks

- Eitaa/Rubika not yet certified (deferred, contract review done, low risk for core product)
- AI provider is external (OpenRouter) — template fallback exists, no critical path dependency
- Weak initial server — resource-efficient design, but load test on target hardware recommended
- Payment is manual V1 — operator must verify, no automatic gateway yet
- Off-site backup destination pending server details (Gate H)

## Open Issues

- None blocking for Telegram+Bale core product
- Eitaa/Rubika as extension after Phase 12 (owner-approved order v1.1)

## Production Blockers

- [ ] Set strong secrets in .env (SECRET_KEY, POSTGRES_PASSWORD, INTERNAL_API_TOKEN)
- [ ] Configure TELEGRAM_BOT_TOKEN, BALE_BOT_TOKEN (shared org bots)
- [ ] Configure AI_PROVIDER=openrouter + AI_OPENROUTER_API_KEY if AI needed
- [ ] Set up TLS (certbot) + nginx
- [ ] Run alembic upgrade head on prod DB
- [ ] Run backup restore test on clean env
- [ ] Run platform certification (scripts/certify_platform.py) for Telegram + Bale
- [ ] Verify /healthz, /readyz, /metrics
- [ ] Load test on target hardware

## Rollback Plan

- Each migration has downgrade (additive migrations, safe to revert)
- App rollback: git checkout previous tag + docker compose build + up -d
- DB rollback: alembic downgrade -1 (only if no data loss, else restore from backup)
- Backup before every production deploy: python scripts/backup.py --type full
- Feature flags via plan feature_flags (runtime tunable, no code change)

## Release

- Version: 0.1.0 (Phase 12 complete for Telegram+Bale core)
- Next: Eitaa/Rubika extension platforms (contract review done, certification pending)
- Tag: v0.1.0-telegram-bale-core
