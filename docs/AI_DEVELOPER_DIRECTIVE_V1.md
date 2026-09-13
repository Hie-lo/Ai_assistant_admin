# AI DEVELOPER DIRECTIVE v1
# دستیار هوشمند کسب‌وکارهای مجازی
Status: Ready for implementation only after explicit project-owner approval
Date: 2026-09-12

## 0. Your role
You are the implementation AI for this repository. Treat the approved project documents as contracts. Do not invent missing requirements silently.

## 1. Non-negotiable rules
1. Read all project docs before modifying code.
2. Design before code.
3. Failure-first: specify normal, partial, failure, duplicate, timeout, stale, permission, restart and recovery cases before implementation.
4. Never make a major architectural change without approval from the project owner.
5. Never use UI visibility as a security boundary.
6. Never trust source row order as product identity.
7. Never silently auto-merge ambiguous products.
8. Never interpret missing source data as confirmed deletion unless the approved policy explicitly permits it.
9. Never assume external APIs provide exactly-once delivery.
10. Never retry an unknown remote publication outcome blindly.
11. Never create unbounded retry loops.
12. Never let AI modify authoritative product facts.
13. Never regenerate historical AI content merely because prompt/model changed.
14. Never log secrets or unnecessary sensitive payloads.
15. Never allow cross-tenant access.
16. Keep cache disposable; critical state must be durable.
17. Keep handlers/controllers thin; domain logic belongs in application/domain layers.
18. Every externally visible side effect needs idempotency and reconciliation behavior.
19. Every production path must be observable and diagnosable.
20. Add tests with every meaningful behavior change.

## 2. Required implementation workflow
For every feature:
A. Read relevant domain docs.
B. State affected entities/states.
C. Enumerate failure and recovery cases.
D. Define authorization and tenant scope.
E. Define idempotency/concurrency.
F. Define external side effects.
G. Define logging/metrics.
H. Define tests.
I. If architecture changes, stop and request approval with rationale, alternatives, impact, risks and rollback.
J. Implement.
K. Run focused + regression + failure tests.
L. Update docs, PROJECT_LOG.md and changelog.

## 3. Repository documentation hierarchy
The authoritative order is:
1. approved MASTER_PROJECT_SPECIFICATION.md
2. approved domain specifications
3. AI_DEVELOPER_DIRECTIVE.md
4. PROJECT_LOG / decisions / changelog
5. source code comments
If documents conflict, do not guess. Stop and flag the conflict.

## 4. Core domains
- User / authentication
- Business / membership / RBAC
- Subscription / payment / entitlements
- Source / mapping / sync
- Product / product version / media / identity
- Content / preset / rendering
- AI output artifacts
- Post / post version
- Publication / publication attempts
- Platform adapters
- Jobs / queue / retry / reconciliation
- Notifications / audit
- Monitoring / backup / recovery

## 5. Critical invariants
- A row number is never a Product identity.
- A Post is not a Publication.
- A Product does not directly equal a remote message.
- Historical Preset/AI/Mapping versions do not mutate silently.
- Unknown remote state is explicit.
- Missing source is non-destructive by default.
- Existing approved AI output is reused.
- One platform failing does not corrupt another platform's publication state.
- An admin removal must stop future authorized actions and invalidate relevant access while retaining audit history.

## 6. Product identity
Prefer trusted stable external/customer identifiers; otherwise use approved identity resolution. Preserve evidence. Ambiguous matches go to review. Do not use name alone as a stable key.

## 7. Source sync
A source sync must first prove that the input was complete and credible before applying missing-row inference. Partial/failed reads must not trigger mass deletion.

## 8. Publication engine
Use explicit state transitions and attempt records. Store remote identifiers. Treat network timeout after a possible accepted request as UNKNOWN until reconciled.

## 9. Platform adapters
Do not duplicate platform rules in core. Adapters declare capabilities and verified limits. Every platform requires certification tests before production activation.

## 10. AI
AI definitions are configuration-driven. Outputs are structured and validated. The renderer owns final formatting. Manual edits/approvals are durable.

## 11. Resource efficiency
Prefer batch reads/writes, short-lived cache, shared workers, bounded concurrency, and compact state. Do not keep a permanent loop/process per customer. Do not store every full source row or full rendered payload forever.

## 12. Security
Use least privilege, explicit account linking, encrypted secrets, tenant scoping, validated inputs, SSRF protection for external fetches, replay-resistant callbacks, and audit logging.

## 13. Testing requirements
At minimum, test:
- reorder/delete/recreate rows
- ambiguous identity
- duplicate jobs/workers
- timeout after remote success
- server restart mid-operation
- remote manual delete/edit
- reconnect after partial publishing
- platform capability differences
- AI retries and invalid output
- preset/mapping version changes
- admin removal during queued/running jobs
- subscription expiry during queued jobs
- cross-tenant authorization attacks
- backup/restore

## 14. Stop conditions
Stop and report instead of guessing when:
- platform API behavior is undocumented or contradicts current certified tests
- identity match is ambiguous
- a migration could destroy or reinterpret historical state
- an external side effect cannot be made safely idempotent/reconcilable
- requirements conflict
- restoring data would risk corruption

## 15. Change reporting template
For every completed meaningful change, report:
- Change ID
- Why
- Files/modules changed
- Data/state impact
- Security impact
- Performance/resource impact
- Tests run
- Results
- New risks
- Rollback plan
- Documentation/log updates
