# MASTER PROJECT SPECIFICATION v1
# دستیار هوشمند کسب‌وکارهای مجازی
Status: Consolidated Draft — requires final owner approval before implementation
Date: 2026-09-12

## 1. Product definition
A public multi-tenant SaaS that imports business products from flexible sources, normalizes them into a stable internal model, renders platform-compatible posts using admin-defined presets, optionally uses AI for editorial content, publishes across messaging platforms, tracks publication lifecycle, synchronizes changes, detects failures, reconciles remote state, and provides owner/admin/web/Bale/Telegram control surfaces.

## 2. V1 scope
### Included
- public account registration/authentication
- business onboarding and business-type selection/correction
- subscription plans, payment workflow abstraction, enforceable entitlements
- owner and owner-approved business admins
- Telegram, Bale, Eitaa and Rubika targets subject to certification
- Telegram, Bale and Web management interfaces sharing one backend state
- Excel and Google Sheets sources
- flexible field mapping and customer confirmation
- official sample templates per business type
- canonical Product/ProductVersion
- Product Media management
- presets/templates with versioning
- default preset per supported business type
- optional/configurable AI descriptions, hashtags and additional AI outputs
- manual AI generate/edit/retry/approve
- automatic AI mode with bounded retry
- preview
- platform-specific render/validate/publish/edit/delete/repost behavior
- product change classification and sync
- manual + scheduled sync
- durable publication tracking, idempotency, concurrency control, reconciliation
- monitoring, structured logs, alerts, audit trail
- automatic encrypted backups and disaster recovery
- admin management of configurable commercial/content/AI settings
- tutorial/training system and onboarding content

### Excluded from V1 core
- full CRM
- autonomous sales/support agent
- autonomous business decisions
- admin recruitment marketplace
- advanced business consulting as a dependency
- Instagram and Website integrations until separately certified

## 3. Product goals
1. Reliable publication/synchronization rather than a general chatbot.
2. Simple customer experience despite flexible customer data.
3. Safe behavior under ambiguity and external failure.
4. Low resource use on the initial weak server.
5. Clean extension points for new business types, data sources, AI outputs and platforms.

## 4. Core architecture principles
1. Source format is not Product identity.
2. Product identity is not Post identity.
3. Post identity is not Publication identity.
4. External side effects target effectively-once behavior, not assumed exactly-once.
5. Unknown state is modeled explicitly.
6. AI is optional and non-authoritative.
7. Platform-specific differences live in adapters/capabilities.
8. Configuration is versioned and auditable.
9. Critical state is durable; cache is disposable.
10. Failure and recovery are first-class design inputs.
11. No per-customer permanent loops/processes.

## 5. User/business model
User accounts are authenticated identities. A Business is an independent business context. Membership binds a User to a Business with a role and granular permissions. Owner can manage admins; admin requests require owner approval.

## 6. Sources
V1 sources: Excel/XLSX and Google Sheets. Each source has a versioned mapping profile and sync policy. The customer is shown a clean recommended sample for the relevant business type, but arbitrary formats remain supported.

Google Sheets should use batch operations where appropriate for efficiency; Google's current API documentation explicitly recommends batchGet/batchUpdate for grouped reads/writes. citeturn926801search0turn926801search2

## 7. Product
Product has immutable internal identity, current durable state, versions/change metadata, media and extensible typed custom fields. Row order is never identity. Ambiguous identity matches are not auto-merged.

## 8. Product disappearance
Missing source rows are non-destructive by default. A partial/failed source read cannot trigger mass deletion. Automatic unpublish is an explicit policy and is not the V1 default.

## 9. Content
Presets are structured, versioned and platform-neutral at the domain level. Each supported business type has a default preset. Custom field tokens and AI output tokens are allowlisted.

## 10. AI
AI is optional editorial assistance. Outputs are named, structured and versioned. Approved outputs are reusable. Historical posts must not receive new AI generations automatically because the model/prompt changes.

## 11. Publication
Post -> PostVersion -> Publication -> PublicationAttempt(s). Publication tracks remote identifiers and explicit state. Unknown remote outcomes require reconciliation before duplicate-prone retries.

## 12. Platform abstraction
Every platform implements an adapter and declares capabilities/limits. No platform is considered production-ready until certification passes.

Current research baseline as of 2026-09-12:
- Telegram official Bot API documents media groups, editing/deletion and chat/admin methods; sendMessage text is limited to 4096 characters after parsing entities. citeturn288425search0
- Bale official docs state the Bot API is based on Telegram's Bot API with changes and document edit/delete methods. IDs can exceed 32-bit and require sufficiently wide storage. citeturn288425search1
- Eitaa public materials indicate EitaaYar/token-based API integration, but current evidence is insufficient to assume feature parity; certify capabilities independently. citeturn613283search0turn613283search2
- Public official evidence located for Rubika in this review is insufficient for a detailed bot API contract; production activation requires authoritative API verification and tests. citeturn288425search2

## 13. Sync and jobs
Manual and scheduled sync use one deterministic pipeline. Jobs run through shared workers/queues with bounded retries, idempotency, concurrency guards, rate limits and dead/final states.

## 14. Security
Required: tenant isolation, least privilege, explicit account linking, platform control verification, encrypted secrets, safe template grammar, input validation, SSRF protection for external fetches, replay-resistant callbacks, auditability and abuse protection.

## 15. Monitoring
Structured logs must answer WHO/TENANT/BUSINESS/PRODUCT/POST/PUBLICATION/PLATFORM/JOB/OPERATION/ATTEMPT/STATE_BEFORE/STATE_AFTER/ERROR/ACTION/RESULT/CORRELATION_ID without secrets.

## 16. Backup/DR
Automatic encrypted backups are mandatory. Local rotation controls disk use. An off-site destination is strongly recommended. Telegram may carry an additional encrypted copy/notification but is not the sole recovery source. Restore tests are mandatory.

## 17. Common SaaS capabilities
V1 foundation should also provide: account/profile settings, notification preferences, support/contact entry point, onboarding/help content, maintenance mode, feature flags, timezone/locale settings, safe data export for customer-owned business data, import job history, session/device visibility and revocation where applicable, and clear terms/privacy consent/version tracking. These are supporting capabilities and must not become a second business domain.

## 17. Training/tutorials
V1 includes editable tutorials/FAQ/onboarding content managed by project admin. Tutorial content is separate from critical domain state.

## 18. Project-admin configurability
Where practical, project admin can manage plans/limits, business types, sample templates, default presets, field definitions, display mappings, AI output definitions/prompts/retry/budget policies, tutorials and report settings. Security invariants and raw infrastructure secrets are not ordinary business configuration.

## 19. Proposed technology stack
See TECHNOLOGY_AND_REPO_SPECIFICATION_V1.md. Proposed baseline: Python 3.13, FastAPI, SQLAlchemy 2.0.x, PostgreSQL 18, Redis, Celery, Jinja2/HTMX, Docker Compose, Alembic, Pydantic v2, httpx, openpyxl and official Google Sheets API clients. SQLAlchemy 2.0.52 is the current stable 2.0 release as of 2026-08-11; the 2.1 line is still release-candidate status on 2026-09-12. citeturn656215search0turn656215search3

## 20. Implementation gates
No foundation implementation until the owner approves identity policy, missing-source policy, RBAC, platform certification order, stack, payment mode, backup strategy and failure matrix.

## 21. Required documents in the delivery package
- Product Domain Specification v2
- Post/Publication Domain Specification v1
- User/Business/Membership/RBAC v1
- Source/Mapping/Sync v1
- Platform Adapter v1
- AI Configuration v1
- Subscription/Payment/Entitlement v1
- Security Threat Model v1
- Observability/Backup/DR v1
- Technology & Repository v1
- Test Strategy v1
- Implementation Roadmap v1
- AI Developer Directive v1
- Final Approval Gates v1
- Final Cross-Domain Review Checklist v1
- Project Changelog & Decisions
