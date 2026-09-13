# Security Threat Model v1
# دستیار هوشمند کسب‌وکارهای مجازی
Status: Draft for final cross-domain review
Date: 2026-09-12

## 1. Security goals
Confidentiality, tenant isolation, integrity of product/publication state, authorization correctness, safe external actions, recoverability, auditable administration.

## 2. Trust boundaries
- Web client
- Telegram interface
- Bale interface
- backend API/application
- worker/queue system
- database
- file/media storage
- third-party platform APIs
- Google APIs
- AI provider
- payment provider
- backup destinations

## 3. Critical assets
- account identities
- business membership/roles
- platform tokens/credentials
- Google OAuth tokens
- subscription/billing state
- product data
- publication mappings/message IDs
- AI credit balance
- backups
- audit records

## 4. Authentication
Messaging identity alone is not sufficient for sensitive cross-interface linking. Web account linking must use an explicit one-time, expiring linkage mechanism. Never trust a user-provided chat/channel identifier as proof of ownership/control.

## 5. Authorization
Use business-scoped roles and server-side permission checks. High-risk actions are Owner-only by default. All sensitive actions are auditable.

## 6. Channel control verification
For each platform, verify that the connected bot/account has the permissions necessary to publish/edit/delete and that the requester has sufficient control. Where technical ownership cannot be proven, label it CONTROL VERIFIED rather than legal OWNERSHIP.

## 7. Secrets
Encrypt credentials at rest using a managed key strategy. Never log raw tokens, OAuth refresh tokens, payment data or full authorization headers.

## 8. Input security
Validate uploaded spreadsheets, URLs, media and template expressions. Enforce file size/type limits, parsing timeouts, decompression limits and sandboxed processing where necessary.

## 9. Template safety
Preset tokens must use an allowlisted grammar. Do not execute arbitrary code from templates or source fields.

## 10. AI safety
Treat source data and AI output as untrusted content. Do not allow prompts or product fields to grant system permissions. AI output cannot directly execute arbitrary tools or SQL.

## 11. SSRF / external fetch protection
If the system fetches image URLs or external sources, restrict schemes, private IP ranges and redirects as appropriate. Do not allow arbitrary internal network access.

## 12. Tenant isolation
Every data access path must be tenant/business scoped. Add database constraints and service-layer checks. Test intentional cross-tenant access attempts.

## 13. Abuse protection
Rate-limit login/linking, source imports, AI calls, manual sync requests and publication actions. Protect against job flooding and malicious large inputs.

## 14. Audit
Record actor, business, action, target, outcome and correlation ID. Avoid storing unnecessary sensitive payloads.

## 15. Recovery security
Backups are encrypted and access controlled. Restore procedures must include integrity verification and secret rotation where compromise is suspected.

## 16. Security acceptance tests
Include privilege escalation, broken object-level authorization, account-link confusion, token leakage, malicious spreadsheet, malicious template, webhook forgery, replayed callbacks, duplicate payment approval, cross-tenant queries, and backup exposure.
