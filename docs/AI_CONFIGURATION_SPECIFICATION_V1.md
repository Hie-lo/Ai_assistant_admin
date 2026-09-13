# AI Configuration & Content Artifact Specification v1
# دستیار هوشمند کسب‌وکارهای مجازی
Status: Draft for final cross-domain review
Date: 2026-09-12

## 1. Role of AI
AI is optional editorial assistance and future advisory capability. It is never the source of truth for price, stock, SKU, identity or technical facts.

## 2. AI Output Registry
AI outputs are configurable definitions, not hard-coded functions. Example definitions:
- ai_description
- ai_recommendation
- ai_short_title
Future definitions can be added through admin-controlled configuration.

## 3. Definition fields
Conceptual definition:
- key
- display name
- prompt template/system instructions
- input fields
- output schema
- validation rules
- max output length
- provider/model policy
- token/cost policy
- retry policy
- allowed contexts/templates
- version
- active flag

## 4. Structured output
AI should return structured data validated against a schema. Presentation formatting is owned by the deterministic renderer.

## 5. Artifacts
Generated output is stored as a content artifact with:
- product_id
- business_id
- output_definition_version
- source dependency fingerprint
- generated value
- approved value/status
- generated/approved timestamps
- model/provider metadata where needed for audit

Full prompts/responses should not be retained indefinitely unless needed for debugging, billing or compliance; retention must be configurable.

## 6. Reuse rule
If an approved artifact remains valid for the same product inputs, output definition version and policy, reuse it.
Published historical posts are NEVER regenerated automatically merely because a prompt/model changed.

## 7. Manual edit
Generated output can be edited before approval and after storage. Manual approved content becomes the selected artifact until explicitly regenerated.

## 8. Manual generation
User chooses a product/output and requests generation. Before generation, entitlement/credits are checked.

## 9. Automatic generation
Automatic mode applies only to eligible new/unsatisfied content. Existing approved content must be reused. Existing historical publications do not trigger AI regeneration.

## 10. Retry
Initial policy: bounded 3 attempts for transient AI failures, with backoff and no loop. Permanent/validation failures enter manual fallback and notification.

## 11. Invalid output
If AI returns malformed/unsafe/incomplete data, do not publish it. Run bounded repair/retry if configured; otherwise require manual correction.

## 12. Token accounting
Consumption must be atomic with the AI job state to avoid double charging. Failed infrastructure calls may be refundable according to billing policy; user-requested rejected generations are not automatically refundable unless policy says so.

## 13. Budget guard
Limits may exist per request, user/business, subscription, day, month and system.

## 14. Template integration
Presets may reference AI output keys using controlled tokens such as {ai_description} and {ai_recommendation}. Missing optional AI output must resolve through an explicit fallback policy.

## 15. Prompt management
Project/Super Admin can edit prompt definitions. Changes create a new version. Prompt versions are auditable and historical artifacts retain their original dependency/version.

## 16. AI is non-critical where avoidable
Price/stock sync, identity resolution, publication state transitions, idempotency and reconciliation must not depend on AI availability.
