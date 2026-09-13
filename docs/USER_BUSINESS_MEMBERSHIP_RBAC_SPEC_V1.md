# User + Business + Membership + Admin + RBAC Domain Specification v1
# دستیار هوشمند کسب‌وکارهای مجازی

Status: Draft for Review / Approval
Date: 2026-09-12

## 1. Purpose

This document defines identity, business ownership, membership, administrator access, authorization, account linking, business switching/correction, and security boundaries for V1.

It is a domain contract. It intentionally does not finalize framework/database/auth-provider choices.

## 2. Core principles

1. A User Account is an identity, not a permanent global Owner/Admin label.
2. A Role is scoped to a Business through Membership.
3. User-selected onboarding role is never itself an authorization decision.
4. Server-side authorization is the security boundary; UI visibility is not.
5. Owner control is business-scoped.
6. An Admin must explicitly request access and the Owner must approve it.
7. Owner can revoke Admin access at any time.
8. Permissions follow least privilege.
9. Sensitive operations are Owner-only by default.
10. Membership changes, role changes, approvals, revocations, and security-sensitive actions are auditable.
11. Account linking across Web, Telegram, and Bale must require explicit proof/control and must avoid accidental account merges.
12. Business identity and Business Type are separate concepts.
13. Correcting a business type must not silently destroy historical business state.
14. Platform connection control is distinct from legal ownership.
15. No authorization decision may rely on mutable display names, usernames, channel titles, or unverified identifiers.

## 3. User Account

Conceptual user fields:
- user_id (immutable internal ID)
- account_status
- authentication identities / verified identifiers
- created_at
- last_activity_at
- security metadata

A user may have multiple verified interfaces/identities associated with the same account, for example Web + Telegram + Bale.

A platform username is an identifier, not proof of identity or ownership.

## 4. Business

Business is a durable tenant/business context with its own identity.

Conceptual fields:
- business_id
- business_name
- business_type_key
- lifecycle_state (ACTIVE / ARCHIVED / ...)
- owner_membership reference concept
- created_at / updated_at

Business Type is configuration/classification and may change without necessarily creating a new Business.

## 5. Membership

Membership connects a User to a Business.

Conceptual fields:
- membership_id
- user_id
- business_id
- role
- status
- permission_set / permission profile reference
- created_at
- approved_at
- revoked_at
- approved_by / revoked_by

Roles in V1:
- OWNER
- ADMIN

Membership status may conceptually include:
- PENDING_REQUEST
- ACTIVE
- REJECTED
- REVOKED
- SUSPENDED

A user cannot gain Owner/Admin privileges by changing a client-side field, username, callback, or onboarding selection.

## 6. Owner invariants

Recommended V1 invariant:
- Exactly one active primary Owner per Business.

Ownership transfer is a high-risk operation and is Owner-only and separately confirmed.

An Owner cannot accidentally remove the final Owner without completing an explicit ownership-transfer/recovery workflow.

## 7. Admin access request

Flow:
1. Admin candidate authenticates.
2. Candidate selects “Request Admin Access”.
3. Candidate provides a resolvable Business reference, approved channel reference, or invitation code.
4. System verifies the target Business exists and that the candidate is not already an active member.
5. System creates a unique pending request.
6. Owner receives request.
7. Owner sees business, candidate identity, requested scope, and verification evidence.
8. Owner approves or rejects.
9. Approval creates ACTIVE ADMIN membership with the approved permission profile.
10. Duplicate/old requests become resolved and cannot be double-approved.

Admin candidate must not be able to approve their own request.

## 8. Linking requests to the correct Business

Prefer direct Business invitation/request references over free-text identifiers when possible.

A platform channel can be used as an additional discovery/verification signal only after its connection is already linked to the target Business.

The system must avoid revealing private business information merely because someone guessed a channel username.

If multiple businesses could match the provided reference, request must enter AMBIGUOUS state and require stronger proof.

## 9. Role selection by user

User may state whether they are:
- Business Owner
- Admin / employee

This is onboarding intent only.

For Owner flow:
- user creates/claims a Business through the approved onboarding and verification flow.

For Admin flow:
- user must obtain Owner-approved Membership; selecting “Admin” alone grants nothing.

A user may have different roles for different Businesses unless a future product policy explicitly forbids it.

The role picker must never modify an existing active Owner/Admin relationship without a secure server-side transition workflow.

## 10. Business type correction

Business Type and Business identity are separate.

Case A — correction of classification:
- Same real business, wrong category selected.
- Change business_type_key.
- Revalidate Business Type dependent configuration.
- Preserve business identity, historical products, publications, audit, and account relationships.

Case B — genuinely new business:
- Create a new Business.
- Existing Business becomes ARCHIVED or remains active if the subscription allows multiple businesses.
- Existing products/publications/connections/history remain attached to the old Business.
- Do not merge business histories automatically.

The UI should distinguish “Correct my business type” from “Create another business”.

## 11. Business switching and multiple businesses

The system should support a user being a member of multiple Businesses if product policy permits it.

Every request must carry an explicit active Business context resolved server-side.

Never infer target Business solely from the last-used interface or message.

Cross-business actions must fail closed when context is missing or ambiguous.

## 12. Admin permissions

Permissions are granular and business-scoped.

Suggested categories:

Products:
- products.view
- products.preview
- products.manage_media
- products.manual_check

Posts:
- posts.view
- posts.create_manual
- posts.preview
- posts.publish
- posts.update
- posts.repost
- posts.delete_remote

Scheduling/Sync:
- sync.view
- sync.run_manual
- schedule.view
- schedule.manage

AI:
- ai.view
- ai.generate
- ai.retry
- ai.edit_output
- ai.approve_output
- ai.toggle_automatic

Connections:
- connections.view
- connections.connect
- connections.reconnect
- connections.disconnect

Business settings:
- business.view
- business.edit_safe_settings

Owner-only by default:
- subscription.manage
- billing.manage
- admin.manage
- owner.transfer
- security.recovery
- sensitive_connection_credentials
- destructive_business_operations

Exact permission matrix requires later approval.

## 13. Permission evaluation

Every protected operation must evaluate:
- authenticated User
- target Business
- active Membership status
- role
- permission
- subscription/entitlement where relevant
- resource ownership/scope
- operation risk

Permissions are checked in backend/domain services before side effects.

## 14. Admin removal

Owner may revoke an Admin at any time.

Revocation should:
- mark membership REVOKED
- invalidate or re-authenticate relevant sessions where supported
- prevent new protected actions immediately
- reevaluate queued work created by that Admin
- preserve audit history

Do not delete the historical Membership row solely because the relationship ended.

## 15. Requests, approval races, and stale approvals

A request can become stale if:
- Owner already rejected/revoked candidate
- Business archived
- candidate already became member by another action
- requested permission profile changed
- security condition changed

Approval must revalidate all prerequisites transactionally before membership activation.

Two simultaneous approvals must result in one valid membership, not duplicate memberships.

## 16. Account linking across interfaces

A single internal User Account may be connected to multiple interface identities.

Example:
User A
- Web identity
- Telegram identity
- Bale identity

Linking must require proof that the same person controls the added identity.

Never link identities based only on matching names/usernames.

## 17. Authentication and session safety

V1 must define:
- secure login/session mechanism for Web
- authenticated mapping for Telegram
- authenticated mapping for Bale
- session expiration/revocation
- suspicious linking protection
- CSRF protection for Web where applicable
- rate limiting for authentication and linking attempts

Exact mechanism is a separate architecture decision.

## 18. Channel ownership/control verification

A User saying “this channel is mine” is insufficient.

Connection must establish:
- the platform resource exists
- the integration has required permissions
- the connecting User has sufficient authority or control signal
- the resource is not already connected to a different Business unless the transfer/recovery flow explicitly supports it

Do not expose whether a resource belongs to another customer beyond what is necessary to provide a safe error.

## 19. Preventing cross-tenant leakage

Every resource access path must be scoped by Business/Tenant ownership.

A valid User session is not enough; the target resource must also belong to the active Business membership.

IDs exposed to clients are not authorization proof.

## 20. Subscription and permissions

Subscription entitlement and authorization are different layers.

Example:
Admin may have `posts.publish`, but if the Business subscription forbids publishing on a given platform or exceeds plan limits, the operation must still be blocked.

Effective permission = role permission ∩ business policy ∩ subscription entitlement ∩ resource scope.

## 21. Business deletion / archival

V1 should prefer ARCHIVE/SUSPEND over destructive hard-delete for Business records containing operational history.

Hard deletion, if ever supported, must have separate rules for:
- personal account data
- business operational data
- billing/legal records
- audit/security evidence
- backups

## 22. Audit requirements

Audit at minimum:
- login/link events
- Business creation/change/archive
- role request/approve/reject/revoke
- permission changes
- connection changes
- sensitive configuration changes
- subscription changes
- destructive actions

Audit events must contain actor, Business, target, timestamp, action, result, correlation ID, and minimal relevant metadata.

## 23. Failure scenarios

Before implementation, tests must cover:
- user chooses wrong role during onboarding
- admin request to nonexistent Business
- admin request to ambiguous target
- duplicate admin request
- simultaneous Owner approval
- admin already has membership
- Owner revoked before approval completes
- admin removed while job is queued
- admin removed while operation is running
- session remains active after revocation
- user switches Business context accidentally
- user attempts cross-business resource access
- identity linking to wrong Telegram/Bale account
- channel already linked elsewhere
- channel verification fails
- business type changed while sync is running
- business archived while jobs are pending
- subscription expires while admin operates
- ownership transfer interrupted

## 24. Non-goals

This document does not finalize:
- authentication provider
- OAuth/Telegram/Bale login mechanism
- exact database schema
- exact permission names
- billing provider
- legal KYC/identity verification
- admin hiring marketplace

## 25. Acceptance criteria

The domain is implementation-ready only when:
1. Owner/Admin invariants are approved.
2. Membership lifecycle is approved.
3. Admin request/approval/revocation flow is approved.
4. Permission matrix is approved.
5. Account-linking flow is approved.
6. Channel control verification rules are approved per platform.
7. Business type correction/new-business behavior is approved.
8. Cross-tenant authorization tests are defined.
9. Failure/recovery outcomes are deterministic.
