# Project Log — دستیار هوشمند کسب‌وکارهای مجازی

## 2026-09-14 — Phase 4 implementation (Content / Presets / AI)

### Delivered
- Domain (pure, framework-free): content block model with ownership
  classification (SYSTEM/CUSTOMER/STATIC/DERIVED), a strict allowlisted
  token grammar ({field}, {attr.<key>}, {ai_<key>}) with injection-safe
  two-pass resolution (values are never re-parsed), deterministic
  rendering, and priority-ordered length handling (identity >
  price/stock > contact > attributes > AI > hashtags > decorative;
  blocks with a clear reason instead of silent truncation). AI policy:
  bounded 3-attempt retry (transient + invalid output), reuse rule for
  APPROVED artifacts by (product, key, definition version,
  declared-inputs fingerprint), refund policy, automatic-mode
  eligibility.
- AI providers behind one contract: deterministic offline TemplateProvider
  (V1 default, Persian-aware, factual — never invents price/stock/SKU) and
  an OpenAI-compatible provider (base URL + API key + model from env; key
  and payloads never logged; 429/5xx/timeout -> transient, 4xx ->
  permanent).
- Presets: business-type presets (structured block lists) with immutable
  versions (DRAFT/ACTIVE/SUPERSEDED) managed by the platform operator; a
  seeded default preset per business type. Per-product presets — a
  completely separate section (own tables/routes/permission) — gated by
  the plan flag product_preset_eligible (top-plan entitlement,
  operator-managed at runtime; Starter off by default).
- AI output registry: versioned, configurable definitions (key, prompt
  template, declared inputs, max length, cost credits); seeded
  ai_description + ai_short_title. Generation flow in one transaction:
  entitlement (plan AI) -> reuse check -> atomic credit consumption ->
  bounded provider attempts -> validation -> PENDING_APPROVAL artifact
  (or refund + manual fallback on total failure). Edit/approve/reject
  lifecycle; manual edit of an APPROVED artifact creates a new PENDING.
- Preview: same deterministic renderer (publication will reuse it in
  Phase 5) resolving product preset (top-plan) -> business-type default
  -> built-in fallback; platform-neutral text + media manifest + per-block
  diagnostics + length fit.
- Automatic mode: business-level toggle + tested eligibility logic (the
  trigger lands with the sync/publication phases per the roadmap).
- HTTP: 18 business/operator endpoints (admin presets + AI definitions;
  business presets, product presets CRUD/assign, AI generate/retry/
  edit/approve/reject/list, automatic toggle, preview). New permission
  product_presets.manage (owner + manager).
- Migration d8e9f0a1b2c3 (additive: 6 tables + 3 columns; seeds AI
  definitions + default presets).
- Tests: suite grows 199 -> 244 locally (unit: token grammar/allowlist,
  injection safety, priority trimming, block render, retry bounds, refund
  policy, reuse rule, template provider determinism/length; integration:
  admin preset lifecycle + versioning + super-admin gate, business preset
  visibility, preview + AI approve flow, reuse without double charge,
  regeneration on input change, transient/invalid/permanent failure with
  credit refund, edit/approve/reject/retry lifecycle, plan-without-AI
  403, no-credits 409, product presets top-plan gate + non-destructive
  downgrade + clear, cross-tenant 404, narrow-profile 403, automatic
  toggle, definition versioning never auto-regenerates).

### Notes
- Real AI credentials (OpenAI-compatible endpoint) are configured
  operationally via env later; the built-in template provider makes the
  feature usable from day one without external calls.
- Per-product presets are deliberately a separate section and a top-plan
  exclusive (owner decision); the plan flag lets the operator move the
  entitlement between plans at runtime without code changes.
- V1 preview is platform-neutral; per-platform previews + actual
  publication reuse this exact renderer in Phases 5-7.

## 2026-09-13 — Phase 3 implementation (Source / Product core)

### Delivered
- Domain (pure, framework-free): identity normalization (NFKC + Persian
  yae/kaf unification + casefold), deterministic fingerprints, and the
  resolution priority external_id -> SKU -> barcode -> fingerprint ->
  name-only-collision; change-risk classification with the approved
  thresholds (price jump >50% = HIGH, identity change = CRITICAL, custom
  spec = HIGH, mass change >=5 products = quarantine, mass missing >50%
  = block).
- Source adapters behind one contract: Excel/XLSX (openpyxl, header
  detection, empty-row skipping, incomplete-read reporting) and Google
  Sheets (injectable client factory, batch values.get, permission/API
  failures reported as incomplete reads — never auto-missing).
- Product model: canonical product (durable UUID, business-scoped), typed
  custom attributes (JSON, metadata from the mapping version), compact
  product versions (change categories + risk, not full snapshots),
  first-class media with SOURCE vs CUSTOMER origin protection, per-source
  row records (locator, not identity), versioned column mapping
  (DRAFT/ACTIVE/SUPERSEDED), review cases, and import runs.
- Import pipeline (manual V1): entitlement gate -> structural read ->
  per-row validation -> identity resolution (pure) -> upsert/create/review
  case -> missing-row inference (only on confirmed complete reads) ->
  ImportRun summary + audit. Idempotent re-sync via row content hash; row
  moves keep identity; ambiguous/conflicting identity NEVER auto-merges;
  identity-field changes are CRITICAL + REVIEW_REQUIRED + frozen; mass
  HIGH-risk changes quarantine the rest of the run; new products respect
  the products entitlement; corrupt reads fail the run without touching
  product state.
- HTTP: 23 business-scoped endpoints (sources CRUD/pause/resume, mapping
  suggest/propose/version/activate, import run/preview/list, products
  list/get/patch/versions, media list/add/remove, archive/restore,
  review cases list/resolve). New manager-level permissions:
  products.import, products.review_mapping, sources.view, sources.manage.
- Entitlement usage counters registered for real: products (non-archived)
  and sources (active+paused).
- Migration c7d8e9f0a1b2 (additive; 8 tables; unique backstops for
  locator-per-source and version-per-product).
- Tests: suite grows 110 -> 199 passing locally (unit: identity full
  matrix + fingerprint rules, change-risk thresholds, Persian/English
  mapping heuristics, Excel adapter, Google Sheets adapter with fake
  client; integration: full import happy path + idempotent re-sync,
  change/risk versioning, missing + reappearance, row move, mass-missing
  block, ambiguous/duplicate/conflict identity -> review case + resolve,
  identity-change freeze, media customer protection (default +
  media_authoritative), invalid rows, corrupt-file no-missing, preview
  zero-write, entitlement gates (product/source/daily-sync),
  cross-tenant 404, permission 403, audit trail; product API: edits,
  versions, custom attributes, media ops, archive/restore).

### Notes
- V1 sync is manual only; the scheduler (periodic sync) lands in Phase 8.
- Google Sheets credentials are configured operationally (service-account
  file path behind credentials_ref); the adapter is fully mock-tested.
- Review cases are the ONLY path by which a quarantined row reaches a
  product; every resolution is audited.

## 2026-09-13 — Phase 2 implementation (Subscription / Payment / Entitlement)

### Delivered
- Plan catalog (operator-managed, no code changes): price/term/limits/AI
  credits/feature flags; seeded "Starter" plan (IRT, tunable at runtime).
- Subscription lifecycle: PENDING/ACTIVE/GRACE/EXPIRED/SUSPENDED/CANCELLED/
  REFUNDED with an explicit audited state machine; one live subscription per
  business; hourly Celery task moves ACTIVE->GRACE (7-day grace, owner
  approved) and GRACE->EXPIRED.
- Manual payments as separate records (spec: payment != entitlement
  activation): verification by the platform operator (Super Admin flag),
  amount-mismatch requires an explicit note (audited), double-verification
  impossible (row lock + pending-payment uniqueness).
- Entitlement engine: derived immediately before use (RBAC spec section 20:
  effective permission = role  policy  entitlement  scope); usage-counter
  registry for Phase 3+ (products/sources/channels/admin seats/storage).
- AI credit ledger: separate monthly + purchased pools, atomic idempotent
  consumption (monthly first), auditable append-only transactions.
- Migration f0e1d2c3b4a5 (additive; Postgres partial unique backstops;
  starter plan seed).
- Tests: suite grows 51 -> 110 passing locally (unit: state machine full
  matrix, calendar clamping, entitlement rules, ledger atomicity/
  idempotency; integration: full lifecycle, grace renewal crossing a month
  with a deterministic clock, same-month no-double-grant, plan change,
  downgrade-over-limit blocking, suspend/reactivate, refund, cross-tenant
  isolation, operator-only access, audit).

### Notes
- Subscription expiry never corrupts data: terminal rows are kept as history;
  a new purchase starts a new row; unused monthly credits are expired (not
  deleted) in the ledger.
- Downgrades do not delete data: over-limit usage blocks NEW operations via
  the entitlement engine until usage is reduced (spec section 8).
- The owner's real plan names/prices still need to be set through the plan
  endpoints (the Starter values are placeholders).

## 2026-09-13 — Phase 1 implementation (Identity / Business / RBAC foundation)

### Delivered
- Domain: enums (account/business/membership/request/platform/lifecycle),
  permission matrix (34 permissions, 7 owner-only), domain errors with stable
  machine codes (cross-tenant access reported as 404 to avoid enumeration).
- Infrastructure: 11 ORM tables (users, user_sessions, businesses,
  business_types, memberships, admin_invites, admin_access_requests,
  channel_links, link_codes, account_identities, audit_logs); Argon2id
  password hashing; session tokens stored only as SHA-256 digests; one-time
  link codes; invite codes (ambiguous characters excluded, hashed at rest).
- Application services: auth (register/login/logout/sessions, bounded
  lockout 5 attempts/15min, timing-equalized unknown-email path), business
  (create with atomic OWNER membership, fail-closed access), membership
  (invite lifecycle, request coalescing, transactional approval
  revalidation, self-approval blocked, revocation revokes all sessions),
  linking (single-use 10-minute codes, cross-account identity conflict ->
  409, idempotent re-link), audit (correlation IDs, actor/business/target).
- HTTP: /api/v1 auth, business, admin, links routes; correlation-id
  middleware; DomainError -> JSON mapping; internal /links/verify contract
  protected by shared token (for the Phase 9 bot).
- First migration `a1b2c4d5e5f6` (additive; seeds business type 'general';
  Postgres partial unique indexes as backstops).
- Tests: 50 passing locally (unit + full-stack integration on in-memory
  SQLite); 2 service smoke tests run in CI against PostgreSQL 17 + Redis 7.
  CI also validates migration DDL on Postgres (`alembic upgrade head`).

### Failure-first behaviors verified by tests
- wrong-password lockout persists across requests (commit-before-raise)
- duplicate admin requests coalesce; single-use invites not consumed by
  rejected submissions (check-then-consume ordering)
- revocation revokes the user's sessions (next call 401)
- cross-business access fails closed with 404
- self-approval impossible; owner revocation of the OWNER blocked
- link code single-use; identity conflict across accounts -> 409
- unknown-email login timing equalized; failed logins audited

### Verification status
- Local: 51 passed / 2 skipped (service smoke), ruff clean.
- CI (run 34763142857, commit a56502f): BOTH jobs GREEN on Python 3.13:
  - Lint + unit (21 tests)
  - Integration: service pre-flight, `alembic upgrade head` on PostgreSQL 17,
    and the full integration suite (32 tests incl. auth lockout, RBAC
    lifecycle, one-time linking codes, audit) against real PostgreSQL + Redis.
- Bug found and fixed by CI: `create_engine(..., poolclass=Pool)` used the
  abstract base pool class — first `connect()` raised `NotImplementedError`.
  Now `QueuePool` explicitly, with a regression unit test.

### Known decisions/notes
- Partial unique indexes are Postgres-specific: enforced in the service
  layer (portable) + migration backstop (Postgres). Not declared in model
  metadata to keep SQLite test schemas valid.
- Business type list seeded with 'general'; the owner's actual business
  types (Understanding Report §10) are still pending.
- PostgreSQL in dev/CI = 17 (conservative fallback); production target 18
  per spec (verified before launch, Gate I).
- Ownership transfer (changing the OWNER) is intentionally not implemented
  yet: separate high-risk workflow (RBAC spec section 6).

### Rollback
Revert the Phase 1 commit; the migration has a full downgrade (drops all
Phase 1 tables). No production data exists yet.

## 2026-09-13 — Phase 1 architecture decisions (owner-approved)

### Decisions (approved by Project Owner, structured approval)
1. **Web authentication mechanism (V1): EMAIL + PASSWORD.**
   - Argon2id password hashing (OWASP-recommended).
   - Server-side sessions (DB-stored, token hash at rest) with expiry and
     revocation; SameSite cookies. Telegram/Bale identities are LINKED to the
     Web account later (not the login path in V1).
   - Login rate limiting: per-user bounded attempts with temporary lockout.
2. **Cross-interface linking: ONE-TIME CODE.**
   - Web panel shows a short-lived 6-digit code; the user sends it in the
     Telegram/Bale bot chat; the bot verifies it against the backend contract.
   - Never based on username/name matching (per RBAC spec section 16).
3. **Admin access request discovery: INVITE CODE (primary) + CHANNEL REFERENCE (secondary).**
   - Invite codes are owner-generated, expiring, use-limited.
   - Channel reference is accepted only when the channel is already linked to
     the target Business; ambiguous matches enter review (never guess).

### Impact
- Phase 1 implementation may proceed: models + migrations, auth, business,
  membership/RBAC, admin request flow, linking contracts, audit foundation.

## 2026-09-13 — Phase 0 completed

### Baseline delivered (commit on arena branch)
- 22-document specification package merged from main and validated (22/22).
- Layered app skeleton (domain/application/infrastructure/interfaces/workers/config).
- Pydantic Settings; SQLAlchemy engine/session (pool_pre_ping); FastAPI factory
  with /healthz + /readyz (separate liveness/readiness); Celery app (bounded).
- Alembic wired to settings + Base.metadata (no migrations yet).
- Tests: 9/9 unit passing locally (Python 3.11); CI runs ruff + unit on
  Python 3.13 and integration on PostgreSQL 17 + Redis 7 services.
- requirements.txt (resolved pins) + requirements.lock.txt (full lock).
  Final lock re-verified on first green CI (3.13) per Gate F note.
- Dockerfile (non-root) + docker-compose.yml (postgres 17 fallback, redis,
  web, worker). Makefile, .env.example, .gitignore, pyproject.toml.
- Rollback: pure scaffold, no data — revert commit.

## 2026-09-13 — Owner Approval of Implementation Gates (A–H)

### Approval record
- Date: 2026-09-13 (owner session, structured approval questions)
- Approved by: Project Owner
- Status: FINAL for the listed scope

### Decisions
1. **Gates A+B+C+D — Domain contracts: APPROVED as documented.**
   Product Domain v2, Post/Publication v1, Source/Mapping/Sync v1,
   User/Business/Membership/RBAC v1, AI Configuration v1 and
   Subscription/Payment/Entitlement v1 are adopted as the implementation
   contracts. Includes: evidence-based identity resolution with no
   auto-merge; safe quarantine default for missing source rows; Owner/Admin
   membership with owner-approved admin requests; versioned Presets/AI/Mapping.
2. **Gate F — Technology stack: APPROVED.**
   Python 3.13 + FastAPI + SQLAlchemy 2.0.x + PostgreSQL 18 (fallback 17) +
   Redis + Celery + Jinja2/HTMX + Docker Compose, per
   TECHNOLOGY_AND_REPO_SPECIFICATION_V1. Exact dependency versions will be
   locked after the first green compatibility run in CI.
3. **Gate E — Platform activation order: APPROVED.**
   Telegram + Bale first (certification, then publication). Eitaa and Rubika
   are certified independently; only certified capabilities activate.
4. **Gate G — Payment mode V1: MANUAL VERIFICATION approved.**
   Owner manually verifies payments and activates subscriptions in V1.
   Automatic gateway (e.g. Shaparak) comes in a later phase behind the
   Payment Provider interface.
5. **Gate H — Backup strategy: APPROVED.**
   Encrypted periodic backups + local rotation + off-site copy + mandatory
   restore testing. Telegram may carry an additional encrypted copy only —
   never the sole DR source. Off-site destination pending server details.
6. **Gate I — Production launch: DEFERRED** to the production phase
   (certification + security review + load test + restore test + failure-matrix pass).

### Impact
- Phase 0 (Approval & baseline) is unblocked.
- Foundation implementation may proceed in roadmap order; Phase 1 =
  Auth / User / Business / Membership / RBAC + audit foundation.

## 2026-09-13 — Project Understanding Report delivered

- AI Developer completed the mandatory bootstrap reading of all 22 package
  documents and delivered `docs/PROJECT_UNDERSTANDING_REPORT_V1.md`
  (14 sections: scope, non-goals, domains, security rules, failure/recovery
  rules, approved vs pending decisions, doc conflicts, gaps, risks,
  implementation order, critical tests, repository readiness).
- Findings: 3 minor doc inconsistencies recorded (Subscription phase order
  between roadmap and bootstrap reference; duplicated section number 17 in
  MASTER spec; permission list overlap between Product spec and RBAC spec —
  RBAC spec is authoritative). No technical blockers.
- READY_TO_PLAN = YES; FIRST_PHASE = Phase 0.
- The 22-document package was uploaded to the repository by the owner under
  `docs/` (flat) and validated document-by-document against the original
  package (titles + content markers, 22/22 pass).

## 2026-09-12 — Product Domain v2 Consolidation

### Scope reinforcement
- V1 remains a public multi-tenant SaaS for product ingestion, content generation, multi-platform publication, synchronization, monitoring, subscriptions, authentication, and Owner-managed Admin access.
- Training/tutorials and payment/subscription capabilities remain required project capabilities, but they are separate domains and must not pollute Product state.
- Product Domain must stay compact, deterministic, durable, explainable, and independent from platform-specific publication implementation.

### Lessons extracted from previous project document
- Previous project documentation was reviewed only as a source of lessons, failure cases, and ideas; its architecture, stack, schema, numerical limits, and implementation patterns are NOT imported automatically.
- Useful retained lessons: recommended business-specific sample files, structured AI output, AI budget control, prompt versioning, preview, health checks, structured logging, backup/recovery, onboarding/tutorial concepts, and graceful degradation.
- Deliberately rejected as automatic carry-over: SKU-as-mandatory identity, row-based identity, boolean publication state, fixed rate limits, scheduler choice, hard-coded AI description model, single-image field, and Telegram-centric architecture.

### New Product Domain v2 requirements
- Recommended input templates will be provided for each supported business type, while arbitrary customer structures remain supported.
- Customers should be encouraged to use stable product IDs, avoid ID reuse and unnecessary row churn, keep one logical product per record, and avoid duplicates. These are recommendations, not system dependencies.
- Product identity is independent of source row position/order.
- Internal Product ID is immutable.
- Source Record is distinct from Product and may move/disappear/reappear.
- Product identity resolution uses an evidence hierarchy and conservative ambiguity handling.
- False merge is treated as more dangerous than false-new-product.
- Identity decisions must be explainable through compact evidence.
- Product Media is a first-class component supporting add/remove/replace/reorder from Customer/Admin panels.
- Product supports stable core fields plus typed business-specific custom attributes.
- Custom fields are template-exposable with formatting rules, e.g. `{touch}` -> `دارد/ندارد`.
- Product version storage must favor current state + hashes + compact change metadata rather than full snapshots on every sync.
- Product deletion/missing-source handling remains non-destructive by default.
- Import must isolate invalid rows and allow valid rows to continue where safe.
- Import Preview / Sync Diff is a required quality and safety feature.
- Suspicious high-risk changes can be isolated/reviewed before downstream propagation.
- AI outputs are separate versioned editorial artifacts, manually editable and reusable after approval.
- Existing published products/posts must NOT receive automatic new AI generations because prompt/model versions change.
- Presets are versioned and historical publications remain tied to their used version.
- Business type correction must not destroy Product data; creation of a genuinely new Business must not mix Products or publication state across Businesses.
- Subscription/entitlement checks gate processing but subscription expiry must not destroy Product data.
- Product permissions are Business-scoped and server-enforced.

### Required Product Domain invariants
1. Row number/order never identifies a Product.
2. Product identity survives source row movement.
3. Product ID is immutable.
4. Missing source data is not immediate deletion.
5. Ambiguous identity never silently merges.
6. Product facts are independent from AI outputs.
7. Product is independent from Post/Publication.
8. Identical repeated syncs are idempotent.
9. One invalid record cannot corrupt unrelated valid records.
10. Critical state is durable, not cache-only.
11. Unknown state remains explicit until resolved.
12. Cross-business/tenant data leakage is prohibited.

### New failure scenarios added
- deleted/recreated source row
- ID reuse
- source column meaning change
- source media vs customer-added media conflict
- business type correction vs actual new business
- custom field lifecycle
- AI artifact reuse/manual override
- import partial validity
- suspicious mass changes
- identity evidence tracking
- Product Media failures and platform-dependent downstream handling

### New/updated artifact
- `PRODUCT_DOMAIN_SPECIFICATION_V2.md` created as Draft for review/approval.

### Approval boundary
No Product foundation/database implementation should begin until Product Domain v2 is explicitly approved or revised by the user, especially:
- identity resolution
- custom field storage
- media model
- missing-source policy
- business-type change rules
- compact version/history strategy
- AI artifact model

## 2026-09-12 — Post & Publication Domain Specification v1 drafted

### New design artifact
- POST_PUBLICATION_DOMAIN_SPECIFICATION_V1.md created.

### Core design principles
- Post is platform-independent; Publication is platform-specific.
- PostVersion and PublicationAttempt are historical/operational evidence and must not be overwritten.
- Remote state uncertainty is explicit and must reconcile before duplicate/destructive actions.
- System targets effectively-once behavior using idempotency, unique constraints, concurrency guards, durable attempts, remote identifiers, and reconciliation.
- Reconnect after partial publication must resume from durable publication state instead of republishing everything.
- Repost is Publish New -> Verify -> Delete Old whenever safe/possible.
- Remote manual edits do not become Product edits in V1.
- Managed content and customer-owned content are separated conceptually.
- Platform capabilities and limits are adapter-specific; no Telegram assumptions may be copied to other platforms.
- Preview should use the same renderer/validator pipeline as publish.
- Historical posts never auto-regenerate AI content because of prompt/model changes.

### Current external verification
- Telegram official Bot API documents 1-4096 characters for text messages after entity parsing, 0-1024 for media captions, media-group sending via sendMediaGroup, caption editing, and media editing. Capabilities are platform-specific and must not be generalized. Source: https://core.telegram.org/bots/api
- Telegram core API documentation also exposes media/message editing details. Source: https://core.telegram.org/api/files
- Eitaa/Rubika capabilities remain subject to official API verification and live adapter tests before production claims.

### Approval gate
- Post/Publication domain is still Draft for Review/Approval.
- Database schema, queue technology, or implementation should not be finalized from this draft alone.

## 2026-09-12 — User/Business/Membership/RBAC Domain Specification v1 drafted

### New design artifact
- `USER_BUSINESS_MEMBERSHIP_RBAC_SPEC_V1.md` created as Draft for Review/Approval.

### Core decisions/proposals
- User is an identity, not a permanent global Owner/Admin classification.
- Roles are scoped to Business via Membership.
- User-selected role during onboarding is intent only and never grants authorization.
- Owner can remove Admin at any time; revocation is auditable and must block future protected actions.
- Admin access is requested by candidate and approved/rejected by the Business Owner.
- Authorization is server-side and combines Membership role, permissions, Business scope, resource ownership, subscription entitlement, and operation risk.
- Business Type is separate from Business identity. Classification correction can preserve the same Business; a genuinely different business should be a new/archived Business context rather than an overwrite.
- Account linking across Web/Telegram/Bale requires explicit proof/control and must prevent accidental identity merges.
- Channel connection must verify technical control and required permissions; usernames/channel names are never sufficient ownership proof.
- Cross-tenant access must fail closed.
- Exact authentication, permission matrix, and platform verification mechanisms remain approval-gated decisions.

### New required failure scenarios
- wrong role selection
- duplicate/stale admin requests
- simultaneous approvals
- admin revocation during queued/running jobs
- cross-business context mistakes
- account linking ambiguity
- channel already linked elsewhere
- business-type correction during active work
- subscription expiry during admin work

### Approval gate
- No implementation of authorization foundations until Membership lifecycle, RBAC matrix, authentication/linking, business switching, and platform control verification are explicitly approved.


## 2026-09-12 — Consolidated design package + final review
- Consolidated implementation package prepared with domain, architecture, security, test, operations and developer-directive documents.
- Added common SaaS foundation capabilities: account settings, notifications, support/help, maintenance mode, feature flags, locale/timezone, safe data export, session/device controls and terms/privacy version tracking.
- Added FINAL_REVIEW_AND_SELF_SCORE_V1.md with an overall planning quality score of 9.8/10.
- No implementation code was written during this planning package phase.


## 2026-09-12 — AI Bootstrap Contract Added

### Added
- Added `00_START_HERE.md` as mandatory entry point for any AI developer receiving the project package.
- Defined interactive phase-by-phase workflow: read -> understand -> plan -> approval -> implement -> test -> review -> log -> owner validation.
- Defined owner approval boundaries, Change Proposal format, Stop/Blocker protocol, Bug Loop prevention, failure-first rules, platform certification rules, documentation rules, Definition of Done, and final review protocol.
- Refreshed project package archive as `PROJECT_PACKAGE_V1_FINAL.zip`.

### Final package intent
- The package is intended to be supplied to an AI coding/development agent as an engineering specification and development-governance package, not as permission to blindly generate the entire system.
- The AI must begin with a Project Understanding Report and must not start foundation implementation before required approvals are obtained.
