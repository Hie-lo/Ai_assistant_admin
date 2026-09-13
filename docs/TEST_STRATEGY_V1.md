# Test Strategy v1
# دستیار هوشمند کسب‌وکارهای مجازی
Status: Required implementation contract
Date: 2026-09-12

## 1. Principle
A feature is not complete when the happy path works. It is complete when normal, failure, duplicate, timeout, restart, permission and recovery cases are tested.

## 2. Test layers
### Unit
Pure domain functions, state transitions, identity resolution, mapping, field normalization, content rendering, policy decisions.
### Integration
PostgreSQL, Redis, object storage, Google Sheets adapter, AI provider adapter, platform adapter stubs.
### Contract
Verify adapter behavior against captured/controlled API contracts and live certification tests.
### End-to-end
Critical user journeys from interface to durable state and side effects.
### Failure/recovery
Crash/restart, timeout, partial success, duplicate worker, permission loss, reconnect and reconciliation.

## 3. Required deterministic fixtures
- clean standard sheet
- arbitrary Persian/English headers
- reordered rows
- deleted/recreated rows
- duplicate candidates
- no ID
- SKU changes
- suspicious specification changes
- missing data
- large dataset
- new custom field
- mapping v1/v2
- preset v1/v2
- AI output v1/v2

## 4. Publication test fixtures
- publish success
- remote success + client timeout
- remote failure
- duplicate task
- concurrent same publication
- remote manual deletion
- remote manual edit
- edit unsupported -> repost
- delete old + publish new ordering
- reconnect after 50+ publications
- server restart mid-batch

## 5. Security tests
- cross-tenant object access
- admin privilege escalation
- revoked admin session
- forged callback
- replayed callback
- invalid channel ownership/control
- token leakage checks
- malicious spreadsheet content
- malicious template tokens
- SSRF via image URL

## 6. Performance tests
Target stages should be measured rather than guessed:
- import throughput
- database query latency
- queue latency
- publish throughput per platform
- AI throughput
- memory usage
- CPU under batch load
- behavior at increasing concurrent tenants

## 7. Release gate
No release if any critical invariant test fails, especially duplicate prevention, tenant isolation, publication reconciliation, backup restore, or permission enforcement.
