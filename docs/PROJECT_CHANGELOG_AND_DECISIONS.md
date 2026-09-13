# Project Changelog & Architectural Decisions
# دستیار هوشمند کسب‌وکارهای مجازی

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
