# Deploy — دستیار هوشمند کسب‌وکارهای مجازی

Status: Phase 10/12 — Production Ready (Telegram + Bale core)

## Files

- `nginx.conf` — reverse proxy with rate limiting, security headers, static serving, TLS placeholder
- `docker-compose.prod.yml` — production override: Postgres 18, resource limits, 2 web replicas, worker, beat, nginx

## Production Deploy Steps (Gate I)

1. Provision server (Ubuntu 22.04+, 2GB RAM recommended, 20GB disk, Docker + Compose)
2. Clone repo, checkout tag v0.1.0-telegram-bale-core
3. Copy `.env.example` to `.env` and fill:
   - SECRET_KEY (long random, 32+ chars)
   - POSTGRES_PASSWORD (strong)
   - POSTGRES_USER, POSTGRES_DB (default postgres, ai_assistant)
   - INTERNAL_API_TOKEN (long random)
   - TELEGRAM_BOT_TOKEN (shared org bot)
   - BALE_BOT_TOKEN (shared org bot)
   - AI_PROVIDER, AI_OPENROUTER_API_KEY if needed
4. `docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml build`
5. `docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml up -d postgres redis`
6. Wait healthy, then `docker compose -f ... exec web alembic upgrade head`
7. `docker compose -f ... up -d web worker beat nginx`
8. Verify:
   - `curl http://localhost/healthz` -> ok
   - `curl http://localhost/readyz` -> ready true
   - `curl -H "Cookie: ..." http://localhost/metrics` (super_admin) -> JSON
   - Web panel: https://yourdomain.com/web/login
   - Bot webhooks: set via Telegram/Bale setWebhook API
9. Run platform certification: `python scripts/certify_platform.py --platform telegram --photo-url https://yourdomain.com/static/certification_photo.jpg`
   And for Bale similarly
10. Run backup: `python scripts/backup.py --type full`
11. Run load test: `python scripts/load_test.py --url http://localhost --concurrency 10 --requests 100`
12. Enable TLS: certbot --nginx -d yourdomain.com, uncomment TLS server in nginx.conf

## Backup & Restore

See docs/BACKUP_RUNBOOK.md

- Daily automatic via Celery Beat
- Manual: `python scripts/backup.py --type full --list --verify`
- Restore: `python scripts/restore.py --file backup_...enc --dry-run` then full restore

## Monitoring

See docs/MONITORING_RUNBOOK.md

- /healthz, /readyz, /metrics (super_admin)
- Structured logs with correlation_id
- Audit logs: /api/v1/businesses/{id}/audit-logs

## Security

See docs/SECURITY_REVIEW.md

- Secrets from env, never logged
- Rate limiting via nginx + middleware
- Security headers
- Tenant isolation audit: `python scripts/tenant_isolation_audit.py`

## Rollback

- App: `git checkout <prev_tag> && docker compose ... build && up -d`
- DB: `alembic downgrade -1` (only if safe) or restore from backup
- Always backup before deploy

## Scaling

- Increase web replicas in docker-compose.prod.yml (2->4)
- Increase worker concurrency (2->4)
- Increase DB pool size in settings (5->10)
- Add read replica (future)
- For >10k products, add trigram index (see SCALE_HARDENING_REPORT)

## Troubleshooting

- /readyz 503: check DB connectivity, logs
- Sync jobs stuck QUEUED: check worker logs, Redis, recovery task
- Publications UNKNOWN: check platform APIs, run reconcile via /api/.../publications/{id}/check
- High latency: check metrics, DB slow queries, queue depth
