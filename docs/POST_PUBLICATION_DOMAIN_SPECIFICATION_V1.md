# Post & Publication Domain Specification v1
# دستیار هوشمند کسب‌وکارهای مجازی

Status: Draft for Review / Approval
Date: 2026-09-12

## 1. Purpose

This document defines the domain rules for turning a Product into platform-specific content, publishing it, tracking its remote representation, updating it, detecting drift, and recovering from failures.

This is a design contract, not an implementation specification.

## 2. Core Principles

1. Product facts and published content are separate domains.
2. A Post is a logical content object; a Publication is a platform-specific remote instance.
3. One Product may have many Publications across platforms/channels.
4. A Publication may have multiple attempts and historical versions.
5. External APIs do not provide guaranteed exactly-once semantics from our side; the system targets effectively-once behavior.
6. UNKNOWN remote outcome is a first-class state.
7. Never blindly retry a potentially successful external operation.
8. Never blindly delete or repost after an ambiguous result.
9. Historical publications must remain tied to the Preset/AI output versions that created them.
10. Platform differences are handled by adapters/capability policies, not scattered conditionals.
11. Customer/manual edits outside system-managed content must be preserved where technically possible.
12. A new AI prompt/model must never automatically regenerate AI content for historical published posts.
13. Preview and publish should use the same content rendering path as closely as possible.
14. Every remote side effect must be auditable and correlated to a durable operation/job.

## 3. Domain Objects

### 3.1 Post

Logical content associated with a ProductVersion and a content configuration.

Conceptual fields:
- post_id
- business_id
- product_id
- product_version_id
- preset_version_id
- content_definition/config version
- status
- created_at / updated_at

Post is platform-independent.

### 3.2 PostVersion

Immutable rendered-content intent or structured content snapshot used for a publication.

Conceptual fields:
- post_version_id
- post_id
- product_version_id
- preset_version_id
- AI output references/version IDs where used
- structured content blocks
- render policy/config version
- content fingerprint
- media manifest fingerprint
- created_at

A historical PostVersion must never be silently rewritten.

### 3.3 Publication

A logical association between a PostVersion and one concrete platform/channel destination.

Conceptual fields:
- publication_id
- post_id
- post_version_id
- platform_connection_id
- remote_message/reference IDs
- current desired state
- current known remote state
- remote fingerprint when available
- active/inactive marker
- created_at / updated_at

### 3.4 PublicationAttempt

One externally visible attempt to create, edit, or delete a remote publication.

Conceptual fields:
- attempt_id
- publication_id
- operation_type
- idempotency_key
- attempt_number
- started_at
- finished_at
- result
- remote_reference if returned
- error classification
- correlation_id

Historical attempts are append-only operational evidence.

## 4. Content Composition Model

A Post is built from structured blocks, not treated as an opaque string only.

Example blocks:
- static_text
- product_field
- custom_field
- ai_output
- hashtag_set
- contact
- date
- conditional_block
- media_reference
- separator

Every block may have a stable block identifier.

Example logical content:

[title block]
[price block]
[stock block]
[ai_description block]
[ai_recommendation block]
[contact block]

This structure enables managed-content tracking, platform-specific rendering, and safe regeneration.

## 5. Managed vs Non-Managed Content

Each content block is classified as:
- SYSTEM_MANAGED
- CUSTOMER_MANAGED
- STATIC
- DERIVED

A system update must change only blocks it owns unless a full-message replacement is unavoidable and explicitly permitted by the platform policy.

Remote manual edits must never be interpreted as Product edits in V1.

If a platform only exposes full-message editing, the adapter must compare/guard against accidental overwrite of protected/manual content where technically possible.

If preservation is technically impossible, the operation must be marked as requiring a defined fallback policy rather than silently destroying content.

## 6. Platform Capability Contract

Each platform adapter exposes capabilities rather than assuming Telegram-like behavior.

Minimum conceptual capability fields:
- can_send_text
- can_send_single_media
- can_send_media_group
- can_edit_text
- can_edit_caption
- can_edit_media
- can_delete
- can_get_remote_state
- can_verify_message_exists
- can_verify_message_content
- max_text_length
- max_caption_length
- max_media_per_publication
- supported_media_types
- formatting_rules
- rate_limit policy
- retry semantics
- permission requirements

Unknown capability must be treated as unsupported/unsafe until verified.

Current research confirms Telegram Bot API supports text send, media groups, caption editing and media editing, while Telegram documents 4096 characters for message text and 1024 characters for media captions after entity parsing. These limits must not be generalized to other platforms. citeturn891987search0turn891987search3

Eitaa/Bale/Rubika capability contracts must be verified by official documentation and integration tests before being marked production-capable. Public evidence for some Eitaa/Rubika behavior is currently insufficiently authoritative to hard-code as a guarantee.

## 7. Rendering Pipeline

Generic flow:

ProductVersion
→ Post configuration
→ AI outputs if explicitly eligible
→ structured blocks
→ platform renderer
→ platform validator
→ final rendered payload
→ preview or publish

The renderer must know:
- field values
- custom-field display rules
- AI outputs
- platform limits
- media policy
- formatting policy
- block priority

## 8. Length Handling

Message/caption length is validated BEFORE external API submission.

Never blindly truncate a generated post.

Content priorities are ordered, for example:
1. Product identity
2. price/availability
3. required contact/CTA
4. essential product attributes
5. approved AI content
6. hashtags
7. decorative/static content

Fallback strategies may include:
- compress optional blocks
- shorten optional AI output within approved constraints
- reduce hashtags
- remove decorative blocks
- split into multiple messages only when platform/business policy explicitly permits it
- otherwise block publication with a clear reason

Each platform must define its own supported strategy.

## 9. Media Rules

Media is part of publication state, not just a URL in Product.

A Product may have an ordered media manifest.

The renderer resolves this manifest against platform capability.

Example:
- Telegram: album may be supported by current API; adapter decides actual publication shape. citeturn891987search0
- Eitaa: do not assume album support; validate first. If only one image is safely supported, the adapter may choose the first eligible image according to approved policy.

A media-set change must be classified separately from caption-only changes.

## 10. AI Output Eligibility

AI generation is an explicit content operation, not an incidental side effect of sync.

Automatic AI generation eligibility requires:
- AI enabled for the business
- relevant AI output missing or explicitly stale according to its dependency policy
- customer has sufficient credits/entitlement
- operation is allowed by the current post workflow

Historical published posts:
- MUST NOT auto-regenerate solely because prompt/model/definition changed.
- MAY be manually regenerated by an authorized user.

If an approved AI output already exists and its dependencies are unchanged, reuse it.

## 11. Post Lifecycle

Conceptual states:
- DRAFT
- PREVIEW_READY
- APPROVED
- QUEUED
- RENDERING
- READY_TO_PUBLISH
- PARTIALLY_PUBLISHED
- PUBLISHED
- UPDATE_REQUIRED
- UPDATING
- REPOST_REQUIRED
- REPOSTING
- DELETE_REQUIRED
- DELETING
- FAILED_RETRYABLE
- FAILED_FINAL
- BLOCKED
- UNKNOWN
- RECONCILING
- ARCHIVED

Exact transitions must be approved before implementation.

## 12. Publication Lifecycle

Conceptual states:
- NOT_PUBLISHED
- QUEUED
- PUBLISHING
- PUBLISHED
- UPDATE_PENDING
- UPDATING
- REPOST_PENDING
- REPOSTING
- DELETE_PENDING
- DELETING
- REMOTE_DELETED
- DISCONNECTED
- PERMISSION_LOST
- FAILED_RETRYABLE
- FAILED_FINAL
- UNKNOWN_REMOTE_STATE
- RECONCILING
- ORPHANED

A boolean `posted` field cannot be the source of truth.

## 13. Publish Operation

Before publish:
1. Check tenant/business access.
2. Check subscription/entitlements.
3. Check platform connection health and permissions.
4. Resolve exact PostVersion/PresetVersion/AI output references.
5. Render.
6. Validate platform limits/capabilities.
7. Acquire concurrency guard.
8. Create deterministic idempotency key.
9. Execute external publish.
10. Persist returned remote identifier and outcome.

If the request times out:
- do NOT classify as definitely failed.
- move to UNKNOWN_REMOTE_STATE.
- reconcile remote state before retrying.

## 14. Exactly-Once Strategy

True exactly-once is not assumed.

Target behavior: effectively-once.

Required mechanisms:
- deterministic Publication identity
- unique constraints
- operation idempotency key
- concurrency locks/guards
- durable attempt records
- remote identifiers
- reconciliation
- explicit unknown state

Example idempotency identity may include:
Business + Product + PlatformConnection + logical operation + PostVersion.

Exact key composition is a later implementation decision.

## 15. Reconnect / Reconciliation

When a connection returns after interruption:

1. Load durable local publication records.
2. Determine desired state.
3. Identify records with known remote identifiers.
4. Query/verify remote state where the platform supports it.
5. Resolve UNKNOWN states.
6. Resume only work that is still required.
7. Never bulk-republish all products solely because connection returned.

For a batch where 50 products were published before disconnect:
- those 50 remain linked to their Publication records.
- reconnect must preserve their identities.
- only unresolved/required products continue.

## 16. Manual Remote Deletion

If a previously published message disappears externally:

Remote state becomes:
REMOTE_DELETED

Do not treat this as:
- product deleted
- source deleted
- publication never existed

Default V1 policy proposal:
- record remote deletion
- keep historical publication evidence
- notify owner/admin according to notification policy
- do not automatically repost unless an explicit business/platform policy allows it

A manually deleted message must never cause unrelated product records to change.

## 17. Manual Remote Edit

V1 principle:
- remote manual edit does not alter Product facts.
- the system preserves customer-owned blocks where technically possible.
- system-owned fields may be synchronized when they are due for a legitimate update.

Example:
Customer manually changes marketing text.
System later changes `{price}`.
Expected behavior:
- price block updates.
- unrelated manually managed text remains when technically possible.

If the platform exposes only whole-message replacement:
- use a structured representation/fingerprint where possible.
- if safe preservation cannot be guaranteed, block/require review rather than silently overwrite.

## 18. Product Change to Publication Decision

Examples:

PRICE_CHANGED
→ update if platform supports it

STOCK_CHANGED
→ update if platform supports it

LOW-RISK presentation change
→ update according to policy

AI output changed manually
→ update only if explicitly requested/eligible

MEDIA_CHANGED
→ platform capability decision

MEDIA_CHANGED + cannot edit
→ REPOST_REQUIRED

PRODUCT_IDENTITY_REVIEW
→ no destructive publication action until resolved

## 19. Repost Rules

A repost is a new remote Publication, not a mutation of the old Publication.

Process:
1. create new PostVersion/publication intent
2. publish new remote message
3. verify successful remote publication
4. persist new remote identifier
5. only then delete old remote publication when policy requires
6. mark old Publication historical/deleted

Never delete the old post first unless the platform/business policy requires it and the risk is understood.

The preferred safe sequence is Publish New → Verify → Delete Old.

## 20. Delete Rules

Deletion has two distinct meanings:
- desired deletion by our system
- remote message already deleted externally

Deleting a Publication must not delete the Product.

Remote delete success must be persisted.

If delete request times out:
- UNKNOWN_REMOTE_STATE
- reconcile before retry

## 21. Partial Batch Failure

A batch is never one indivisible transaction with the external platform.

Example:
100 products scheduled
70 published
10 failed retryable
5 unknown
15 not started

The system must preserve exact per-publication state.

Retry only eligible items.

One failed publication must not roll back 70 successful external publications.

## 22. Scheduling

Scheduling belongs to operation/job domain, not Product.

A Publication may be scheduled according to:
- customer schedule
- business schedule
- platform policy
- subscription limits

Scheduled work must reference immutable PostVersion/PresetVersion inputs.

Changing a preset must not silently alter queued content unless an explicit reschedule/re-render policy says so.

## 23. Preview

Preview must be generated through the same renderer/validator stack used for actual publication as closely as practical.

Preview should show platform-specific output.

For multi-platform publishing, preview should expose differences where output differs.

Example:
Telegram Preview
Bale Preview
Eitaa Preview

## 24. Platform Failure Isolation

One platform failing must not block unrelated platforms.

Example:
Telegram publish succeeds.
Eitaa fails.
Bale is queued.

These become three independent Publication states.

## 25. Remote State Uncertainty

Unknown outcomes include:
- timeout after send
- connection loss after API acceptance
- server crash after external success but before DB update
- partial remote response

UNKNOWN_REMOTE_STATE is not retryable by default.

Recovery path:
UNKNOWN → RECONCILING → confirmed state → next valid transition.

## 26. Orphan Handling

An orphaned Publication may occur when:
- Product is archived while a publication remains
- Business configuration is changed
- Connection is removed
- Historical data is retained but active desired state disappears

Orphaned does not mean safe to delete automatically.

## 27. Historical Immutability

Historical records must retain references to:
- ProductVersion
- PostVersion
- PresetVersion
- AI output versions used
- Platform connection
- publication attempts

Changing current configuration does not rewrite history.

## 28. Resource Efficiency

Do not retain full remote payloads for every attempt unless needed for debugging/recovery.

Prefer compact:
- fingerprints
- IDs
- status
- error codes
- changed fields
- references to immutable definitions

Rendered content may be retained when needed for exact publication/recovery, but retention must be bounded.

## 29. Error Taxonomy

At minimum classify:
- AUTHENTICATION_ERROR
- PERMISSION_ERROR
- NOT_FOUND
- RATE_LIMITED
- VALIDATION_ERROR
- LENGTH_LIMIT
- MEDIA_UNSUPPORTED
- PLATFORM_UNSUPPORTED_OPERATION
- NETWORK_TIMEOUT
- NETWORK_ERROR
- REMOTE_UNKNOWN
- DUPLICATE_GUARD
- CONCURRENCY_CONFLICT
- INTERNAL_ERROR

Each error must retain machine-readable code + human-readable diagnosis + operation context.

## 30. Required Logging Context

Every publication operation must be traceable by:
- tenant/business
- product
- post
- post version
- publication
- platform
- connection
- job
- attempt
- operation
- state before
- state after
- idempotency key/fingerprint
- correlation ID
- error code
- recovery action
- result

Never log access tokens/secrets.

## 31. Acceptance Scenarios Before Implementation

At minimum test:

1. publish success
2. duplicate job
3. duplicate worker
4. timeout after likely remote success
5. remote success + local DB failure
6. edit success
7. edit remote success + local failure
8. delete success
9. delete remote success + local failure
10. manually deleted remote post
11. manually edited remote post
12. disconnected after 1 post
13. disconnected after 50+ posts
14. reconnect without duplicates
15. media change requiring repost
16. media change editable remotely
17. platform text-length overflow
18. platform caption-length overflow
19. unsupported media group
20. one platform failure while others succeed
21. preset version changes after publication
22. preset changes while jobs are queued
23. AI output already exists
24. AI fails 3 times
25. AI prompt changes after historical publication
26. product price changes
27. product stock changes
28. product high-risk field changes
29. source mapping changes
30. product becomes missing from source
31. subscription expires while queued
32. admin removed while operation pending
33. server restarts during publish
34. worker restarts during publish
35. reconciliation after restart
36. remote message cannot be verified
37. old publication is replaced by new repost
38. old publication delete fails after new publish succeeds
39. two concurrent repost requests
40. cross-tenant access attempt

## 32. Non-goals

This document does not finalize:
- exact database schema
- exact queue technology
- exact platform implementation
- exact identity of remote messages on unsupported APIs
- final deletion/unpublish policies
- final business subscription values

Those require separate approved design documents.

## 33. Approval Criteria

This domain is ready for implementation only when:

1. Post/PostVersion relationship is approved.
2. Publication state machine is approved.
3. Idempotency strategy is approved.
4. Reconciliation strategy is approved.
5. Remote deletion/edit policy is approved.
6. Repost ordering is approved.
7. Platform capability contract is approved.
8. Length/media fallback policy is approved.
9. AI output reuse rules are approved.
10. Failure and recovery acceptance scenarios are approved.
