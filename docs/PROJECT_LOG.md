# Project Log — دستیار هوشمند کسب‌وکارهای مجازی

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
