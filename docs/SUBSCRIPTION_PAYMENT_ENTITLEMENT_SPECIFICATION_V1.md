# Subscription / Payment / Entitlement Specification v1
# دستیار هوشمند کسب‌وکارهای مجازی
Status: Draft for final cross-domain review
Date: 2026-09-12

## 1. Purpose
Define commercial access without embedding plan logic throughout the application.

## 2. Separation
Catalog Plan -> Price/Term -> Subscription -> Entitlements -> Usage/Consumption.
Payment transaction is separate from entitlement activation.

## 3. Plan configuration
Super Admin can configure, without code changes where practical:
- plan name
- price
- billing period
- product limit
- source limit
- channel/connection limit
- sync frequency bounds
- AI monthly credits
- AI availability
- preset customization level
- report level
- storage/media limits
- admin seat limit if introduced
- feature flags

## 4. Subscription states
PENDING, ACTIVE, GRACE, EXPIRED, SUSPENDED, CANCELLED, REFUNDED where needed.
Transitions are explicit and auditable.

## 5. Payment
V1 may support manual payment verification if necessary, but payment provider integration must be isolated behind a Payment Provider interface. No card secrets belong in normal application tables/logs.

## 6. Entitlement check
All expensive or privileged operations check current entitlement server-side immediately before execution. UI visibility is not a security boundary.

## 7. Expiry behavior
Subscription expiry must not corrupt product/publication history. New writes may be blocked according to policy while existing data remains recoverable. Queued jobs must be re-evaluated before external side effects.

## 8. Upgrade/downgrade
Changing plan updates entitlements without rewriting historical publications. Downgrade cannot silently delete data; it may prevent new operations until usage is reduced.

## 9. AI credits
Monthly and purchased credit pools are separate ledgers. Consumption is atomic/idempotent. Credit history remains auditable.

## 10. Business-independent account records
Billing history belongs to the account/customer context and must survive a business archive/change where legally/operationally appropriate.
