# Monitoring & Observability Runbook — دستیار هوشمند کسب‌وکارهای مجازی

Status: Phase 10 — Operational
Date: 2026-09-16

Per OBSERVABILITY_BACKUP_DR_SPECIFICATION_V1.

## 1. Structured Logs

Every significant operation carries correlation_id and records:
WHO / TENANT / BUSINESS / PRODUCT / POST / PUBLICATION / PLATFORM / JOB /
OPERATION / ATTEMPT / STATE_BEFORE / STATE_AFTER / ERROR_CLASS / ACTION / RESULT

Implementation:
- Middleware in app.py: adopts X-Correlation-Id or mints uuid4.hex
- Stored in context var for audit/logs
- Request logging: method, path, status, duration, correlation_id, user_id, business_id
- NEVER logs cookies, tokens, bodies, secrets (rule 14, 19)

Example:
```
http request correlation_id=abc123 method=POST path=/api/v1/businesses/.../sources/.../sync status=202 duration_ms=45 user_id=... business_id=...
```

## 2. Metrics

### In-process registry (app/infrastructure/monitoring/metrics.py)

- Thread-safe Counter, Gauge, Histogram
- No external dependency for V1 (low resource)
- Exposed via /metrics (JSON, super_admin) and /metrics/prometheus (text)

Required metrics (spec section 3):
- job backlog/age: sync_queued, sync_running gauges
- sync duration/failure rate: http_request_duration_ms histogram, sync failure counters
- publish success/failure rate: publication attempt counters
- unknown remote state count: publications_unknown gauge
- duplicate-prevention conflicts: duplicate guard counters
- API rate-limit events: http_rate_limited_total
- AI success/latency/cost: ai_generate counters
- database connections/latency: db_products_total, etc.
- CPU/RAM/disk: via host monitoring (future: node_exporter)
- backup success/restore-test status: backup_created_total, backup_last_size_bytes

### Endpoints

- GET /metrics — JSON snapshot (super_admin)
- GET /metrics/prometheus — Prometheus text (future scrape)
- GET /healthz — liveness (always 200 if process alive)
- GET /readyz — readiness: DB + Redis checks, 200=ready, 503=not ready

## 3. Health Checks

- /healthz: liveness — version, environment, status ok
- /readyz: readiness — database SELECT 1 + Redis PING
  - database: ok / unavailable: <error>
  - redis: ok / degraded: <error> (Redis not critical for V1 readiness)
  - Returns 200 if DB ok, 503 otherwise

Nginx and Docker healthchecks use /healthz.

## 4. Audit Logs

- Table: audit_logs
- Fields: actor_user_id, business_id, action, target_type, target_id, outcome, correlation_id, meta_data, created_at
- Actions: login/link events, business creation/change/archive, role request/approve/reject/revoke, permission changes, connection changes, sensitive config, subscription changes, destructive actions
- Endpoint: GET /api/v1/businesses/{id}/audit-logs (business.view permission)
- Retention: bounded (operator policy, default 90 days, not implemented in V1 code — future cleanup task)

## 5. Alerts (Operational)

Critical alerts must be actionable and readable. Include likely cause, affected tenants/products, first occurrence, recurrence, suggested runbook.

V1 alerts (manual or via external monitoring):

- No backup in 26h -> critical, check backup task logs
- /readyz 503 -> check DB connectivity
- sync_queued > 100 -> worker down or overload
- publications_unknown > 10 -> reconcile needed, check platform APIs
- http_rate_limited_total spike -> possible abuse, check IP
- backup_last_size_bytes drop >50% -> possible data loss, verify

Future: integrate with Prometheus Alertmanager or Telegram bot notifications.

## 6. Middleware

- MetricsMiddleware: records http_request_duration_ms histogram, http_requests_total counter, http_rate_limited_total
- RateLimitMiddleware: in-memory 120 req/min per IP per sensitive path (login, links, businesses)
  - Sensitive prefixes: /api/v1/auth/login, /api/v1/auth/register, /api/v1/links, /api/v1/businesses
  - Returns 429 with code RATE_LIMITED
  - For multi-worker, use Redis-backed limiter (future)
- SecurityHeadersMiddleware: X-Content-Type-Options, X-Frame-Options, X-XSS-Protection, Referrer-Policy, CSP

## 7. Dashboards (Future)

- Grafana dashboard with:
  - Request rate, latency p50/p95/p99
  - Sync job backlog, success rate
  - Publication success rate per platform
  - DB size, connection count
  - Backup age, size
  - Error rate by class

V1: use /metrics JSON + logs.

## 8. Log Retention

- Retention bounded (operator policy)
- Debug-level payload logging disabled in prod by default
- Secrets and unnecessary source data never logged
- Logs to stdout (Docker) -> collected by host journald or Loki

## 9. Failure Scenarios

- DB down: /readyz 503, logs show ImportError or connection error, alert
- Redis down: /readyz shows degraded, queue tasks fail, recovery dispatcher reclaims
- High latency: histogram p95 spike, check DB slow queries, queue depth
- Rate limit hit: 429 response, metric inc, client should backoff
