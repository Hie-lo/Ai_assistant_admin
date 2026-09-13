# Final Cross-Domain Review Checklist v1
# دستیار هوشمند کسب‌وکارهای مجازی
Status: Required before implementation approval
Date: 2026-09-12

## 1. Identity consistency
[ ] User identity is separate from Business identity.
[ ] Membership/role scope is explicit.
[ ] Product identity is separate from source row identity.
[ ] Publication identity is separate from Product identity.

## 2. Version consistency
[ ] Mapping versions preserved.
[ ] Product versions preserve material states.
[ ] Preset versions preserve historical rendering.
[ ] AI output definition versions preserve historical artifacts.

## 3. Failure consistency
[ ] Unknown remote outcome never becomes proven failure automatically.
[ ] Missing source never equals confirmed deletion automatically.
[ ] Partial source read never triggers mass deletion.
[ ] Platform failure does not block unrelated tenants.
[ ] Retry cannot create unbounded loops.

## 4. Publication consistency
[ ] Product -> Post -> Publication -> Attempt relationship is deterministic.
[ ] Edit unsupported means explicit repost path.
[ ] Remote deletion is distinguishable from never-published.
[ ] Reconnect performs reconciliation, not bulk blind republish.
[ ] Duplicate worker execution is idempotent.

## 5. AI consistency
[ ] AI cannot change product facts.
[ ] Existing approved AI content is reused.
[ ] Historical posts are not auto-regenerated after prompt changes.
[ ] Manual edit/approval is durable.
[ ] AI failure degrades gracefully.

## 6. Security consistency
[ ] Every sensitive use case performs server-side authorization.
[ ] Account linking is explicit and expiring.
[ ] Channel control verification is platform-specific.
[ ] No secrets in logs.
[ ] Templates cannot execute code.

## 7. Operations consistency
[ ] Every job has correlation ID.
[ ] Errors map to actionable categories.
[ ] Backups are encrypted and restorable.
[ ] Restore has a tested runbook.
[ ] Resource cleanup policies exist.

## 8. UX consistency
[ ] Telegram, Bale and Web expose the same backend state.
[ ] Customer is not forced to understand platform-specific internals where automation is safe.
[ ] Preview uses the production rendering path.
[ ] Sync diff is understandable.
[ ] Ambiguous cases are isolated and explainable.
