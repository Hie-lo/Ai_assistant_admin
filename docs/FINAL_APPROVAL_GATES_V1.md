# Final Approval Gates v1
# دستیار هوشمند کسب‌وکارهای مجازی

The following are proposed decisions, not silently approved decisions.

## Gate A — Domain
Approve Product v2 and Post/Publication v1 contracts.

## Gate B — Identity
Approve the exact Product Identity Resolution policy, thresholds and ambiguity handling.

## Gate C — Missing source
Choose the initial default: safe quarantine/manual review is recommended; automatic destructive unpublish is not the default.

## Gate D — Roles
Approve business-scoped Owner/Admin membership model and the default permission matrix.

## Gate E — Platforms
Approve activation order. Recommended: certify Telegram + Bale first; certify Eitaa and Rubika independently before production activation.

## Gate F — Stack
Approve the proposed lightweight FastAPI/SQLAlchemy/PostgreSQL/Redis/Celery/Jinja2+HTMX/Docker stack, with exact versions locked after compatibility tests.

## Gate G — Payments
Choose the initial payment mode/provider. The payment integration must remain behind an adapter and must not be mixed with subscription state logic.

## Gate H — Backups
Approve encrypted local rotation + off-site backup + optional Telegram copy/notification + restore testing.

## Gate I — Production launch
Before production: platform certification, security review, load test, restore test and failure-matrix test suite must pass.
