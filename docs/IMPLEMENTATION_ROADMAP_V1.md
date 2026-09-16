# Implementation Roadmap v1
# دستیار هوشمند کسب‌وکارهای مجازی

## Phase 0 — Approval & baseline
- Approve final gates
- freeze domain contracts
- initialize Git repository
- add docs and CI skeleton
- establish migrations/test infrastructure

## Phase 1 — Identity / Business / Auth
- User
- Business
- Membership/RBAC
- authentication/account linking
- audit foundation

## Phase 2 — Subscription / Payment
- plan catalog
- subscription lifecycle
- entitlement engine
- payment adapter
- credit ledger

## Phase 3 — Source / Product core
- Source
- Mapping
- import pipeline
- validation/data quality
- Product/ProductVersion/Media
- identity resolution

## Phase 4 — Content
- preset registry/versioning
- default presets
- custom field rendering
- AI output registry/versioning
- manual AI generate/edit/approve/retry
- preview

## Phase 5 — Telegram certification + publication
- adapter contract
- platform capabilities
- publish/edit/delete
- publication state machine
- idempotency
- reconciliation
- tests

## Phase 6 — Bale certification + publication
- independent certification
- adapter activation
- capability-specific rendering

## Phase 7 — Eitaa/Rubika certification
**Status: DEFERRED to after Phase 12 (owner decision 2026-09-16 — core-first).**
- current official docs review — DONE
  (`PHASE7_EITAA_RUBIKA_CONTRACT_REVIEW.md`: Rubika official REST API fully
  reviewed incl. 2-step media upload; Eitaa has no public official bot API —
  third-party eitaayar.ir contract captured; capability matrix vs
  Telegram/Bale complete)
- live/staging tests — PENDING (run when the phase resumes)
- unsupported capability matrix — DONE (see review doc)
- activate only passed features

## Phase 8 — Sync engine
- manual sync
- scheduler
- queue workers
- retries/backoff
- change classification
- reconnect reconciliation

## Phase 9 — Interfaces
- Telegram management interface
- Bale management interface
- Web panel
- unified permission/use-case layer

## Phase 10 — Monitoring / Backup / DR
- structured logs
- metrics/alerts
- health/readiness
- encrypted backups
- restore automation/runbook
- migration test

## Phase 11 — Scale hardening
- load tests
- query/index tuning
- queue tuning
- rate-limit tuning per platform
- tenant isolation audit
- resource usage profile on weak server

## Phase 12 — Production readiness
- security review
- failure matrix pass
- backup restore pass
- platform certification pass
- rollback rehearsal
- release checklist

## Execution order v1.1 (owner-approved 2026-09-16)

Core-first strategy: finish a clean, complete Telegram + Bale product before
adding new platforms.

1. Phase 8 — Sync engine
2. Phase 9 — Interfaces (Telegram/Bale management + Web panel)
3. Phase 10 — Monitoring / Backup / DR
4. Phase 11 — Scale hardening
5. Phase 12 — Production readiness
6. Eitaa/Rubika — AFTER Phase 12, as extension platforms. The contract
   review is already complete (`PHASE7_EITAA_RUBIKA_CONTRACT_REVIEW.md`);
   what remains is the live certification runs + adapter build, so the
   deferred phase is smaller than it was.
