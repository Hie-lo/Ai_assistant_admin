# Project Changelog & Architectural Decisions
# دستیار هوشمند کسب‌وکارهای مجازی

## 2026-09-15 — Phase 6: Bale platform decisions approved + adapter delivered
Owner approved (5 structured questions, 2026-09-15):
1. BALE bot model: ORGANIZATIONAL SHARED BOT — the same model as
   Telegram (one platform-level bot per platform; token from env,
   never stored per business, never logged; the owner adds the bot as
   admin to its channel and connects the target).
2. Media: albums up to 10 (Bale documents sendMediaGroup but NOT a max
   item count — 10 is the conservative Telegram-equivalent until the
   live certification measures Bale's own limit).
3. Markdown: ESCAPE special characters on the wire. Bale parses EVERY
   message as markdown (bold/italic/links), so unescaped product text
   containing `* _ [ ] ( )` would be reinterpreted; the adapter
   escapes them (with an exact unescape inverse for reconciliation).
4. 48h delete limit: LINGERING TRACKED STATE. Bale only allows deleting
   messages younger than 48h; when the old message of a repost is too
   old, the new message stays live and the old publication is tracked
   (FAILED_FINAL, remote id kept) for manual owner deletion in the app.
5. Activation: adapter + fake-client tests ship now; the operator runs
   the LIVE certification script (`scripts/certify_platform.py`, spec
   section 13 checklist: getMe, getChat, getChatMember admin,
   sendMessage, editMessageText, sendPhoto, sendMediaGroup,
   editMessageCaption, deleteMessage, optional 48h probe + rate probe)
   on the server as the gate before enabling Bale publication.

Implementation notes (architectural):
- New multi-platform core `app/infrastructure/platforms/base.py`:
  PlatformError / PlatformClient / PlatformCapabilities + a lazily
  built per-platform client registry. The publication core no longer
  imports Telegram; it resolves client + capabilities by
  `connection.platform` (adding Eitaa/Rubika in Phase 7 is now one
  adapter module + one registry entry).
- `send_photo` added to the semantic surface: single media uses
  sendPhoto on BOTH platforms (Bale's single-photo caption is 4096
  while album items are 1024 — the declared capabilities are now real
  on the wire).
- Bale has NO message-lookup method: `inspect_remote=False`. Publish
  verification = API-accepted send; `check` is an explicit conflict
  (no silent fallback, spec section 8); suspended publications resume
  after (re)verification with an attempt record noting the missing
  inspection.
- State machine: DELETING -> FAILED_FINAL is now a legal transition
  (a non-retryable delete failure — e.g. Bale's 48h limit, or a 403
  delete on Telegram — must land in a tracked final state instead of
  crashing the request).
- Settings: `BALE_BOT_TOKEN`, `BALE_API_BASE_URL`
  (default `https://tapi.bale.ai`), `BALE_REQUEST_TIMEOUT_SECONDS`.
- Tests: +41 (16 unit + 20 integration + assertions); suite 308 -> 349
  passing; ruff clean.

## 2026-09-15 — OpenRouter selected as the external AI provider

Owner direction (2026-09-15): the external AI token provider for this
deployment is OpenRouter. Implemented as a first-class provider option:
`AI_PROVIDER=openrouter` + `AI_OPENROUTER_BASE_URL` / `_API_KEY` /
`_MODEL` (OpenAI-compatible gateway; vendor/model slugs; recommended
X-Title attribution). Key handling follows the standing rule —
environment only, never stored, never logged. OpenRouter 402
(insufficient credits) is classified PERMANENT (not retried; credit
refunded per AI spec section 12), 401/400 likewise; timeout/429/5xx
stay transient with the bounded 3-attempt budget. The built-in
template provider remains the dev/test default (offline, no keys);
OpenRouter is enabled per deployment by environment.

## 2026-09-15 — readyz import error fixed (production finding)
The owner ran the build on the server and hit an import error on
`/readyz`. Root cause: the readiness handler's lazy import
(`from app.infrastructure.db import get_engine`) referenced a symbol the
package `__init__` did not re-export, so EVERY /readyz call failed with
`unavailable: ImportError` (masked as a DB problem by design of the
check). Fix: re-export `get_engine`; regression test added asserting
200/ok with a live engine (the old contract test allowed 503 and masked
the bug).

## 2026-09-15 — Phase 5 platform/publication decisions approved
Owner approved (6 structured questions, 2026-09-15):
1. Publish mode V1: MANUAL only (explicit owner action per product per
   connection). Automatic/scheduled publishing and the sync engine land
   in Phase 8 and will drive the same publication state machine.
2. Bot model: ORGANIZATIONAL SHARED BOT — the platform manages ONE
   Telegram bot (token configured operationally via env, never stored
   per business, never logged). The business owner adds the bot as
   ADMIN to its own channel/group and connects that target; verification
   proves control (getMe -> getChat -> getChatMember admin). (The
   per-business-bot-token alternative was explicitly declined.)
3. Media: album up to 10 photos per post (Telegram media group); when
   media is present the text becomes the CAPTION (<=1024), plain text
   <=4096 otherwise. The Phase 4 renderer trims by approved priority and
   blocks (never silently truncates) when essentials don't fit.
4. Product changes: MANUAL "update" trigger with AUTOMATIC
   classification NOOP (no-op) / EDIT (in-place text/caption edit) /
   REPOST (media change or edit unsupported -> new message). No
   auto-apply in V1.
5. Remote manual deletion by the owner in Telegram: recorded as
   REMOTE_DELETED (post archived) and NEVER auto-reposted (spec section
   16 V1 policy).
6. Repost safe order: publish NEW -> verify -> delete OLD (spec section
   19). The old message is never deleted first; if verification of the
   new message fails, the old one stays live and the new one is
   reconciled later (the interrupted final step is completed at that
   time).

Model decisions (documented for the record):
- Adapter contract: the core uses semantic operations + a capability
  matrix (Telegram: 4096/1024/10, edit/delete/inspect supported); HTTP
  errors are classified into the platform taxonomy (401/403/404/429/400
  + timeout/network); response bodies and credentials never leave the
  adapter (rule 14).
- Publication state machine: 17 explicit states; a timeout is UNKNOWN_
  REMOTE_STATE (never a failure); UNKNOWN must reconcile, never
  auto-retry as a fresh publish; a publish timeout that lost the message
  id stays explicitly unresolved (no remote search in V1) while the
  duplicate guard still prevents a blind re-publish.
- Invariant: at most ONE PUBLISHED publication per (connection,
  product), enforced by the state machine AND a PostgreSQL partial
  unique index; the old publication leaves PUBLISHED before the
  replacement enters it (remote order unchanged: new -> verify -> old).
- Permission loss / disconnect SUSPENDS every publication that holds
  (or may hold) a remote message; (re)verification RESUMES suspended
  publications by reconciling each one individually — never
  bulk-republishing (adapter spec section 15).
- Idempotency: deterministic publish key per (business, product,
  connection, post version) with a unique column backstop (effectively-
  once, spec section 14); every remote operation leaves a durable
  attempt record (spec section 30).
- Entitlement: connecting uses the plan's channels limit (Starter: 2,
  runtime-adjustable); a DISCONNECTED connection frees its slot.
- Migration e9f0a1b2c3d4: additive (5 tables + indexes + partial unique
  backstop).

## 2026-09-14 — Phase 4 content/AI decisions approved
Owner approved (6 structured questions; 5 recommended + 1 extension):
1. AI provider: deterministic built-in template generator NOW (offline,
   no keys) + a ready OpenAI-compatible endpoint configured operationally
   later via env (same pattern as Google Sheets credentials).
2. Presets: global, versioned presets per business type (project-admin
   managed, seeded defaults) — PLUS owner extension: OPTIONAL per-product
   presets in a COMPLETELY SEPARATE section (own tables/routes/permission),
   exclusive to the TOP-tier plan (plan flag product_preset_eligible,
   operator-managed at runtime; Starter starts off).
3. AI cost: 1 credit per generation (regardless of length); infrastructure
   failures that produce no artifact are refunded; user rejection is NOT
   refunded (AI spec section 12).
4. Automatic mode: toggle + eligibility logic now; the actual trigger
   lands with the sync/publication phases per the roadmap (no auto
   generation in Phase 4).
5. Approval: explicit human approval required (PENDING -> APPROVED);
   unapproved AI output never reaches preview/publication; editable before
   and after storage (editing an APPROVED artifact creates a new PENDING).
6. Preview: platform-neutral render + length diagnostics now;
   per-platform previews arrive with the adapters (Phase 5), same renderer.

Model decisions (documented for the record):
- Presets are structured BLOCK lists (typed + ownership-classified), not
  opaque strings; historical versions are immutable (DRAFT/ACTIVE/
  SUPERSEDED); one default preset per business type.
- Token grammar is a strict allowlist: {product_field}, {attr.<key>},
  {ai_<definition_key>}; parsed once, values substituted as inert data
  (prompt-injection safe); unknown tokens rejected at save time.
- Renderer = the single deterministic path for preview AND publication
  (spec sections 7/23); length handling trims by approved priority
  (identity > price/stock > contact > attributes > AI > hashtags >
  decorative) and BLOCKS with a clear reason rather than silent truncation.
- AI reuse: only an APPROVED artifact for the same (product, key,
  definition version, declared-inputs fingerprint) is reused; a
  prompt/model change never auto-regenerates existing content; declared
  inputs only participate in the fingerprint (price edit does not
  invalidate a description artifact).
- Generation = one transaction: consume credit -> bounded 3 attempts
  (transient + invalid-output retryable, permanent stops) -> validate ->
  store PENDING; total failure refunds the credit (no orphaned
  deductions); AI never writes product facts.
- Product-preset resolution in preview: assigned product preset (when
  plan-eligible + active version) overrides the business-type default;
  downgrade/missing degrades to the default with a warning —
  non-destructive; clearing an assignment never requires entitlement.
- New business permission product_presets.manage (owner + manager);
  business-type presets + AI definition registry are platform-operator
  (super admin) actions.
- Migration d8e9f0a1b2c3: additive (6 tables + 3 columns), seeds the
  default AI definitions (ai_description, ai_short_title) and a default
  preset per business type.

## 2026-09-13 — Phase 3 source/product core decisions approved
Owner approved (6 structured questions, all recommended options):
1. Identity algorithm: priority external_id -> SKU -> barcode ->
   deterministic fingerprint -> (name-only collision = duplicate candidate
   for review). AMBIGUOUS / IDENTITY_CONFLICT always create a review case;
   never auto-merge.
2. Fingerprint composition: name + category + first technical spec;
   fallback name + first 120 normalized chars of description. Name-only is
   NEVER a confident identity rule.
3. Custom fields: typed JSON inside Product (attributes); field
   type/metadata (type, display name, required, template exposure) comes
   from the mapping version — no per-field schema migrations in V1.
4. Media V1: URL references (no server file storage). Customer-added media
   (origin=CUSTOMER) are protected from source refresh by default
   (source.media_authoritative=false).
5. Missing rows: non-destructive. A row missing from a confirmed complete
   read moves the product to MISSING_FROM_SOURCE (no deletion);
   reappearance reconnects automatically. >50% of the baseline missing in
   one read blocks missing inference (MASS_MISSING_BLOCKED review case).
6. Google Sheets adapter implemented now (mock-tested via an injectable
   client factory); real OAuth/service-account credentials are configured
   operationally later (credentials_ref, secrets never logged).

Model decisions (documented for the record):
- Row position (locator) is a locator, NOT identity. Row moves keep the
  product identity through the identity fields.
- Idempotent re-sync: a row whose normalized mapped content is unchanged
  (row content hash) produces no new version.
- Identity-field changes (external_id/sku/barcode) are always CRITICAL
  risk, create a review-required state, and retain the old identifier in
  the version row (reversible). Products in REVIEW_REQUIRED are frozen for
  automatic updates.
- Mass change quarantine: the same HIGH-risk field changing on >=5
  products in one run quarantines the remaining rows of that run
  (SUSPICIOUS_CHANGE review case; already-applied changes remain applied).
- Suspicious price jump: relative change >50% is HIGH risk.
- V1 sync is MANUAL only (scheduler is Phase 8); at most one RUNNING
  import per source (duplicate -> 409); plan limit
  sync_frequency_per_day enforced per business per UTC day.
- Mapping is versioned (DRAFT/ACTIVE/SUPERSEDED); changing a mapping never
  mutates an old version; a superseded version cannot be re-activated.
- New permissions (manager-level, not owner-only): products.import,
  products.review_mapping, sources.view, sources.manage.
- Entitlement usage counters registered for real: "products" (non-archived
  count) and "sources" (active+paused count) in the domain registry.

## 2026-09-13 — Phase 2 subscription/payment/entitlement decisions approved
Owner approved (structured questions):
1. Plans V1: full catalog mechanism + one seeded "Starter" plan with sensible
   placeholder limits; the owner tunes prices/limits at runtime through the
   plan-management endpoints (no code changes).
2. Currency: Toman (IRT).
3. Grace period: 7 days after the billing period ends (GRACE -> EXPIRED).
4. Manual payment verification is performed by the PLATFORM OPERATOR
   (Super Admin), not by the business owner. This adds a platform-level
   `users.is_super_admin` flag, independent of business RBAC.
Model decisions (documented for the record):
- One live (non-terminal) subscription per business; one PENDING payment per
  subscription (service layer + Postgres partial unique index backstops).
- Plan change is an attributes change, not a lifecycle transition; renewal
  inside the same calendar month reuses the monthly credit pool (no double
  grant); cross-month renewal expires the old pool and grants the new one.
- Credit consumption priority: monthly pool before purchased pool; refunds
  go to the purchased pool (they survive month rollover).
- Entitlements are derived immediately before privileged operations; Phase 3+
  registers usage counters per limit key in the domain registry.

## 2026-09-13 — Phase 1 identity/auth decisions approved
Owner approved: (1) Web auth V1 = email + password with Argon2id hashing and
server-side expiring/revocable sessions; Telegram/Bale linked later via
one-time codes. (2) Cross-interface linking = one-time code (never username
matching). (3) Admin request discovery = invite code (primary) + already-linked
channel reference (secondary), ambiguous matches go to review.
Phase 0 baseline (scaffold, CI, migrations infra, Docker) completed the same day.

## 2026-09-13 — Implementation gates approved (A–H)
Project Owner approved the implementation gates via structured approval:
domain contracts (A+B+C+D) as documented; technology stack (F) as proposed;
platform activation order (E): Telegram + Bale first, Eitaa/Rubika certified
independently; payment mode V1 (G): manual verification, automatic gateway
later behind the Payment Provider interface; backup strategy (H): encrypted
backups + local rotation + off-site copy + mandatory restore testing.
Gate I (production launch) remains a production-phase gate.
From this point forward, foundation implementation proceeds in roadmap order
(Phase 1 = Auth/User/Business/Membership/RBAC + audit foundation).

## 2026-09-12 — Initial vision consolidation
The project was reframed from a generic AI business assistant into a focused multi-tenant product publishing and synchronization SaaS.

## 2026-09-12 — Failure-first principle
Major features must define failure, ambiguity, duplicate, timeout, restart and recovery behavior before implementation.

## 2026-09-12 — Product identity separation
Source rows, row order and SKU are not universal product identities. Stable internal product_id is required. Ambiguous matches cannot auto-merge.

## 2026-09-12 — Source safety
Missing source rows do not automatically mean deletion. Partial/invalid source reads cannot trigger mass removal decisions.

## 2026-09-12 — Publication reliability
Exactly-once external behavior is not assumed. The project targets effectively-once behavior with idempotency, unique constraints, concurrency control and reconciliation.

## 2026-09-12 — AI policy
AI is optional editorial assistance. Existing approved AI output is reused. Historical publications are not automatically regenerated after prompt/model changes.

## 2026-09-12 — Configurable AI outputs
AI outputs are defined by configuration and versioned; examples include ai_description and ai_recommendation.

## 2026-09-12 — Custom product fields
Typed custom attributes such as touch are supported and can be rendered through controlled template tokens.

## 2026-09-12 — Preset versioning
Preset changes do not mutate historical publications or queued work unexpectedly.

## 2026-09-12 — Platform isolation
Each platform has its own adapter and capability matrix. No cross-platform capability assumptions.

## 2026-09-12 — Eitaa verification note
Public material indicates EitaaYar/token-based API integration, but current public evidence does not justify assuming full Telegram parity. Eitaa must be certified with current documentation and live tests before production activation.

## 2026-09-12 — Bale verification note
Official Bale documentation states the Bot API is based on Telegram Bot API with changes and documents edit/delete methods. Exact limits still require adapter testing.

## 2026-09-12 — Telegram verification note
Official Telegram Bot API currently documents message editing/deletion/media groups and a 4096-character text limit for sendMessage. Use the adapter capability contract rather than scattering constants through business logic.

## 2026-09-12 — Backup strategy
Automatic encrypted backups are mandatory. Local rotation limits disk usage. Off-site copies are required. Telegram may be an additional copy/notification channel but never the sole backup source. Restore tests are mandatory.

## 2026-09-12 — Previous project lessons
The previous failed project was analyzed only for lessons and blind spots. Its code, schema, fixed limits and technology choices are not authoritative for the current project.

## 2026-09-12 — Current status
Domain and implementation-package drafts exist. No database schema, stack or major architecture is approved until the project owner explicitly approves the relevant design gates.
