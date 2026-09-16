# Security Review — دستیار هوشمند کسب‌وکارهای مجازی

Status: Phase 12 — Production Readiness
Date: 2026-09-16

Per SECURITY_THREAT_MODEL_V1 and FINAL_APPROVAL_GATES_V1 Gate I.

## 1. Security Goals

Confidentiality, tenant isolation, integrity of product/publication state,
authorization correctness, safe external actions, recoverability, auditable administration.

## 2. Trust Boundaries

- Web client (browser, HTMX)
- Telegram interface (bot webhook)
- Bale interface (bot webhook)
- Backend API/application (FastAPI)
- Worker/queue system (Celery + Redis)
- Database (PostgreSQL)
- File/media storage (URL-based V1, S3 future)
- Third-party platform APIs (Telegram, Bale, Eitaa, Rubika)
- Google APIs (Sheets)
- AI provider (OpenRouter)
- Payment provider (manual V1)
- Backup destinations (local, S3, Telegram optional)

## 3. Critical Assets

- Account identities (users, sessions)
- Business membership/roles
- Platform tokens/credentials (TELEGRAM_BOT_TOKEN, BALE_BOT_TOKEN)
- Google OAuth tokens (credentials_ref, not secret itself)
- Subscription/billing state
- Product data
- Publication mappings/message IDs
- AI credit balance
- Backups (encrypted)
- Audit records

## 4. Implemented Controls

### Authentication

- Web: email + password, Argon2id hashing (OWASP-recommended), server-side sessions (SHA-256 digest at rest), SameSite cookies, expiry, revocation
- Login rate limiting: per-user bounded attempts with temporary lockout (5 attempts/15min) + IP-based middleware (120 req/min)
- Messaging identity alone not sufficient for sensitive linking — Web account linking uses explicit one-time expiring code (LinkCode, 10min, single-use, SHA-256 at rest)
- Never trust user-provided chat/channel identifier as proof of ownership

### Authorization

- Business-scoped roles (OWNER, ADMIN) via Membership
- Granular permissions (34 permissions, 7 owner-only)
- Server-side enforcement in every protected operation (RBAC spec section 13)
- Effective permission = role ∩ business policy ∩ entitlement ∩ scope
- Owner-only permissions stripped from ADMIN profiles (resolve_profile)
- Cross-tenant access fails closed with 404 (not 403, to avoid enumeration)
- Audit trail for sensitive actions

### Channel Control Verification

- For each platform, verify bot has required permissions (getMe -> getChat -> getChatMember admin)
- Behavioral proof for Rubika/Eitaa (successful send = admin check)
- Legal ownership separate from technical control (CONTROL VERIFIED label)
- No secret stored per business — shared org bot token from env

### Secrets

- Encrypted at rest where needed (backup Fernet, session token hash)
- Never logged (rule 14, 19)
- Environment only: SECRET_KEY, TELEGRAM_BOT_TOKEN, BALE_BOT_TOKEN, AI_OPENROUTER_API_KEY
- .env git-ignored, .env.example documents required vars

### Input Security

- File upload validation: max 10MB, allowed extensions (xlsx, jpg, png, webp, gif), content-type check
- Spreadsheet parsing: openpyxl with decompression limits, empty row handling, incomplete-read reporting
- Template safety: strict allowlist grammar {field}, {attr.<key>}, {ai_<key>}, injection-safe two-pass resolution
- URL validation for media: SSRF protection (validate_external_url blocks private IPs, localhost, blocked schemes file/ftp/gopher/data, suspicious patterns)
- Text sanitization: remove control chars, truncate, block script tags in business names

### AI Safety

- Source data and AI output treated as untrusted content
- Prompts don't grant system permissions
- AI output cannot execute arbitrary tools or SQL
- Provider interface isolates vendor; template provider default (offline, deterministic)
- Failure classification: timeout/429/5xx transient (bounded retry), 4xx permanent, no body leakage

### SSRF / External Fetch Protection

- validate_external_url blocks private IP ranges (127.0.0.0/8, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 169.254.0.0/16, ::1, fc00::/7, fe80::/10)
- Blocks localhost, file://, ftp://, gopher://, data://
- sanitize_url_for_log removes secrets from logs
- Media fetch: should use validated URLs only (future: enforce in product media add)

### Tenant Isolation

- Every data access path business-scoped (require_business_access, get_product, etc.)
- Database constraints: partial unique indexes (Postgres backstop) + service-layer checks (portable)
- Tests: cross-tenant 404 for products, sources, connections, publications, sync jobs, audit logs
- IDs exposed to clients are not authorization proof

### Abuse Protection

- Rate limiting: login/linking (10r/m), API (60r/m), general (120r/m) via nginx + middleware
- File size limits, parsing timeouts
- Job coalescing: at most one active sync per source (DB lock + idempotency key)
- Publication duplicate guard: deterministic idempotency key + unique index
- Queue: task_acks_late, prefetch 1, bounded retries

### Audit

- Actor, business, target, timestamp, action, result, correlation ID, minimal metadata
- No sensitive payloads
- Endpoints: /api/v1/businesses/{id}/audit-logs

### Recovery Security

- Backups encrypted (Fernet) and access controlled (backups/ git-ignored, 700 perms)
- Restore includes integrity verification (checksum) and secret rotation where compromise suspected
- Manifest records app version, schema version for compatibility checks

## 5. Security Tests (Required)

- Cross-tenant object access (products, sources, connections, publications, sync jobs) -> 404
- Admin privilege escalation (ADMIN cannot use owner-only perms) -> 403
- Revoked admin session -> 401
- Forged callback / replayed callback (bot webhook) -> handled via secret verification (future)
- Invalid channel ownership/control -> fails closed
- Token leakage checks (no token in logs, responses)
- Malicious spreadsheet content (formula injection, large file, invalid types) -> isolated
- Malicious template tokens (unknown tokens rejected at save)
- SSRF via image URL (private IP blocked)

All above covered by existing integration tests + new security tests in tests/failure/.

## 6. Known Limitations & Future Hardening

- Bot webhook secret verification: Telegram supports secret_token header, currently best-effort (allow if no secret configured). Production should set TELEGRAM_WEBHOOK_SECRET and enforce.
- Redis rate limiter is in-memory per worker; multi-worker needs Redis-backed limiter
- File upload virus scan: not in V1, future ClamAV or similar
- CSP: currently allows unsafe-inline for styles (HTMX simplicity); tighten in future
- 2FA: not in V1, future TOTP
- Session fixation: mitigated via token hash + expiry, but no rotation on privilege change (future)
- CORS: not needed for V1 (same origin), but if SPA added, configure explicitly

## 7. Production Checklist (Security)

- [ ] SECRET_KEY set to long random value (>=32 chars)
- [ ] DATABASE_URL with strong password
- [ ] TELEGRAM_BOT_TOKEN, BALE_BOT_TOKEN from env, never in code
- [ ] AI_OPENROUTER_API_KEY from env
- [ ] INTERNAL_API_TOKEN set to long random
- [ ] .env not committed, 600 perms
- [ ] backups/ 700 perms, encrypted
- [ ] nginx TLS configured (certbot)
- [ ] Rate limiting enabled (nginx + middleware)
- [ ] Security headers (nginx + middleware)
- [ ] /metrics protected (super_admin)
- [ ] Audit logs retention configured
- [ ] Restore test passed
- [ ] Platform certification passed (Telegram + Bale)
- [ ] Dependency audit (pip audit)
- [ ] Ruff + tests green
