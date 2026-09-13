# PROJECT START DIRECTIVE
# دستیار هوشمند کسب‌وکارهای مجازی

## Mission
Build a reliable, extensible, resource-efficient multi-tenant SaaS for product ingestion, multi-platform publication, synchronization, AI-assisted editorial content, and business/admin management.

## Non-negotiable engineering rules
1. Design before code.
2. Failure-first: edge cases and recovery paths are designed before implementation.
3. No major architectural change without explicit user approval after presenting rationale, alternatives, impact, risks, and rollback.
4. Prefer deterministic logic over AI when deterministic logic is sufficient.
5. External operations are never assumed exactly-once; design for effectively-once behavior and reconciliation.
6. No boolean-only state for complex workflows.
7. No UI-only security controls.
8. No cross-tenant data access.
9. No infinite retry loops.
10. Do not blindly delete, repost, or resync after ambiguous external failures.
11. Do not log secrets or unnecessary sensitive payloads.
12. Optimize resource usage deliberately; scale through jobs/workers rather than one process per customer.
13. Every production operation must be observable and diagnosable.

## V1 capabilities
- Public registration/authentication.
- Business creation and later correction/change.
- Subscription plans and enforceable entitlements.
- Multiple platform connections per Business.
- Telegram, Eitaa, Bale, Rubika initial target; Instagram/Website later.
- Telegram Panel, Bale Panel, and Web Admin Panel over one unified backend state.
- Excel + Google Sheets ingestion.
- Customer-selected enabled/disabled columns.
- Automatic field suggestions + customer confirmation/correction.
- Versioned reusable mapping profiles.
- Canonical Product and ProductVersion models.
- Preset-based content generation.
- Optional AI descriptions and hashtags.
- Manual AI generation and automatic AI mode.
- Bounded AI retry + manual fallback.
- Saved approved AI content reuse.
- Preview.
- Platform-specific rendering and validation.
- Publishing/edit/repost/delete according to platform capability.
- Product change detection/classification.
- Manual and scheduled sync.
- Durable publication tracking.
- Idempotency/concurrency/reconciliation.
- Owner-approved admin access requests.
- Business-scoped granular admin permissions.
- Owner can remove admins at any time.
- Monitoring, structured diagnostics, alerts, audit trail.

## V1 explicit exclusions
- Product editing from bot.
- Full CRM.
- Autonomous sales/support agent.
- Autonomous business decision making.
- Admin recruitment marketplace.
- Advanced business consulting as a core dependency.

## Required workflow for every major feature
A. Define goal and scope.
B. Enumerate normal + edge + failure + recovery cases.
C. Define entities and states affected.
D. Define permissions.
E. Define idempotency/concurrency behavior.
F. Define external side effects.
G. Define logs/metrics/alerts.
H. Define tests.
I. Present architecture-changing decision for approval.
J. Implement only after approval.
K. Test normal + failure + restart + duplicate + concurrency cases.
L. Update PROJECT_LOG.md and relevant docs.

## Core domain concepts to design first
User
Business
Membership
Subscription
PlatformConnection
Source
SourceMapping
Product
ProductVersion
Preset
Post
PostVersion
Publication
PublicationAttempt
SyncJob
AIJob
Notification
AuditLog

## Critical failure scenarios that must be specified before coding
- Source row disappears.
- Source temporarily unavailable.
- Product identity changes.
- Important field changes accidentally.
- Product has already been published vs never published.
- Bot is disconnected after partial publishing.
- Bot is removed from a channel.
- Required permissions disappear.
- Publish request times out with unknown remote result.
- Remote publish succeeds but local DB update fails.
- Remote edit/delete succeeds but local update fails.
- Duplicate worker/job execution.
- Server crash/restart during operation.
- Mapping changes while sync is active.
- Preset changes while jobs are queued.
- Subscription expires while work is queued.
- AI succeeds/fails intermittently.
- AI generated content already exists and should be reused.
- Platform-specific message limit is exceeded.
- Platform cannot edit a media/content change.
- Reconnect after long downtime must not create duplicates.
- Owner removes admin while admin has active sessions/jobs.
- Admin request targets wrong Business/channel.
- User account linking ambiguity.
- Customer connects a platform resource they do not control.

## Decision gate
Do not finalize database schema, queue technology, framework, or deployment topology until the failure/domain workshop has produced an approved model for the above scenarios.

## Failure-first scenarios that MUST be designed before implementation

The project must explicitly model and test at minimum:

1. Google Sheet rows are deleted and recreated in different positions.
2. A source has no stable product ID.
3. Two products have similar names/attributes and identity matching is ambiguous.
4. A product's price changes.
5. A product's high-risk specification changes accidentally (e.g. CPU model).
6. A product disappears from source temporarily.
7. A product is genuinely removed from source.
8. A published remote message is manually deleted.
9. A published remote message is manually edited.
10. The bot loses access after publishing dozens/hundreds of products.
11. The bot reconnects after an incomplete publication batch.
12. A publish request times out after the platform may already have accepted it.
13. An edit succeeds remotely but local persistence fails.
14. A delete succeeds remotely but local persistence fails.
15. A preset changes after publication.
16. A mapping changes while synchronization is running.
17. An AI output already exists and must be reused instead of regenerated.
18. A new custom field appears and must become available to templates.
19. An administrator is removed while work is queued or running.
20. A backup succeeds but restore fails.
21. The server is lost and the application must be restored elsewhere.
22. A customer changes business type or creates a new business and historical state must remain coherent.

## Core invariant
When the system cannot safely determine an external state, it MUST preserve an explicit unknown/ambiguous state and reconcile it before taking a destructive or duplicate-prone action.

## Phase 0.6 — Product Domain Specification
Before database implementation, formally approve:
- Product identity and source-record separation
- Identity resolution and ambiguity behavior
- Missing/deleted/recreated product policy
- Product versioning and compact storage strategy
- Custom field and typed attribute strategy
- AI output artifact/version/reuse rules
- Remote post manual-edit policy
- Required failure/recovery acceptance tests

Mandatory principle: no implementation based on guessed behavior when a state is ambiguous. Prefer explicit UNKNOWN/AMBIGUOUS states plus reconciliation/review over destructive guesses.
