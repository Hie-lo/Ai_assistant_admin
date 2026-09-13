# Project Changelog & Architectural Decisions
# دستیار هوشمند کسب‌وکارهای مجازی

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
