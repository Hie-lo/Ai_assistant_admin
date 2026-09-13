# Observability / Backup / Disaster Recovery Specification v1
# دستیار هوشمند کسب‌وکارهای مجازی
Status: Draft for final cross-domain review
Date: 2026-09-12

## 1. Structured operation log
Every significant operation carries a correlation_id and records:
WHO / TENANT / BUSINESS / PRODUCT / POST / PUBLICATION / PLATFORM / JOB / OPERATION / ATTEMPT / STATE_BEFORE / STATE_AFTER / ERROR_CLASS / ACTION / RESULT.

## 2. Error taxonomy
Errors are classified as:
- validation
- authentication
- authorization
- configuration
- source
- platform API
- rate limit
- timeout/unknown outcome
- database/storage
- queue/worker
- AI provider/output
- subscription/entitlement
- internal unexpected

Each class has severity and retry policy.

## 3. Monitoring
Minimum metrics:
- job backlog/age
- sync duration/failure rate
- publish success/failure rate
- unknown remote state count
- duplicate-prevention conflicts
- API rate-limit events
- AI success/latency/cost
- database connections/latency
- CPU/RAM/disk
- backup success/restore-test status

## 4. Alerting
Critical alerts must be actionable and readable. Include likely cause, affected tenants/products, first occurrence, recurrence count and suggested runbook.

## 5. Health checks
Separate liveness from readiness. A live process that cannot reach its database/queue must not be reported as fully ready.

## 6. Log retention
Retention is bounded. Debug-level payload logging is disabled in production by default. Secrets and unnecessary source data are never logged.

## 7. Backup design
Recommended: encrypted periodic full backup + compact incremental/differential backups when operationally justified + off-site copy.
Local retention is short and rotated.

## 8. Telegram backup copy
Telegram can receive a backup notification and/or encrypted backup artifact as an additional off-site copy if practical, but it is never the sole disaster-recovery source. The system must not depend on Telegram availability to complete normal backups.

## 9. Backup manifest
Each backup records:
- timestamp
- application version
- schema version
- backup type
- checksum
- encryption/version metadata
- retention class
- required restore components

## 10. Restore
Restore must be able to rebuild the application on a new server. It includes database, necessary media/configuration and version compatibility checks.

## 11. Restore test
A successful file creation does not count as a successful backup. Periodic automated or operator-run restore tests must verify that a clean environment can recover the service.

## 12. Migration runbook
New server workflow:
1. provision compatible runtime
2. restore database
3. restore required media/config
4. restore secret references/rotate as needed
5. run migrations/compatibility checks
6. run integrity checks
7. run smoke tests
8. enable traffic/jobs
9. reconcile external publications
10. verify monitoring and backups
