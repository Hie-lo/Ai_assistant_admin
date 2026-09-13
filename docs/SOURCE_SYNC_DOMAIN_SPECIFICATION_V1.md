# Source / Mapping / Sync Domain Specification v1
# دستیار هوشمند کسب‌وکارهای مجازی
Status: Draft for final cross-domain review
Date: 2026-09-12

## 1. Purpose
Define how external product sources such as Excel and Google Sheets are connected, mapped, validated, synchronized and recovered without making source row position the product identity.

## 2. Source types
V1:
- Excel/XLSX upload
- Google Sheets connection
Future sources must implement the same Source Adapter contract.

## 3. Recommended source format
The system MUST provide a clean sample template for every supported business type. The sample is recommended, not mandatory.
Recommended structure should contain:
- stable product ID when practical
- name
- price/currency
- stock/status
- description
- media references
- business-specific fields
- documented field types and examples
The system must remain usable with non-standard sources.

## 4. Source connection
A Source has durable identity and configuration, including type, external reference, selected sheet/range, credentials reference, status, mapping profile, sync policy and timestamps.
Secrets are never stored in logs.

## 5. Import pipeline
Source -> connectivity check -> structural read -> column discovery -> customer enable/disable -> suggested mapping -> customer correction -> validation -> preview/diff -> confirmation -> active mapping -> normalization -> identity resolution -> product changes.

## 6. Column rules
- Disabled columns are excluded from downstream ingestion.
- Unknown columns are inert until explicitly enabled/mapped.
- Duplicate headers are detected and require disambiguation.
- Empty or malformed required values create row-level errors, not necessarily whole-sync failure.
- Column rename/order change is not automatically a semantic change if mapping still resolves.

## 7. Mapping Profile
Mapping is versioned and business/source scoped. A mapping contains field target, source column identity, type, normalization, validation and display policy.
Changing a mapping creates a new version. Historical Product/Post artifacts retain their old mapping/version references.

## 8. Sync modes
- Manual: user requests immediately.
- Scheduled: system runs when due.
- Event-assisted: source notifications can trigger/accelerate a sync where technically available.
Scheduler must create due jobs; workers execute them. No permanent loop per customer.

## 9. Sync transaction model
A sync is a job with:
- sync_id
- source_id
- mapping_version
- requested_by / trigger
- status
- started/finished timestamps
- correlation_id
- counts: read/valid/new/changed/missing/ambiguous/blocked/error
- failure summary
A sync must be resumable or safely repeatable.

## 10. Row movement
Row number, sheet position and ordering are never identity. A row moved from 10 to 900 is unchanged if identity resolves to the same Product.

## 11. Product identity evidence
Identity resolution should use, in order:
1. trusted customer ID/external ID when available
2. stable source key
3. business-configured safe composite key
4. controlled similarity/fingerprint fallback
Evidence must be stored compactly enough to explain the decision.
Never auto-merge ambiguous matches.

## 12. Duplicate source rows
Duplicate candidates are isolated. The system must not publish duplicate products because two source rows appear similar. Customer/admin review can resolve them.

## 13. Missing rows
A row absent from a successful complete sync becomes MISSING_FROM_SOURCE, not automatically DELETED.
If the source read was partial, interrupted, permission-denied, truncated or suspicious, missing-row inference MUST NOT run.
A configurable policy may later archive/unpublish after repeated confirmed absence, but destructive action is not the default.

## 14. Reappearance
A reappearing row is matched back to the prior Product when identity evidence is sufficiently strong. Otherwise it creates a review/ambiguous case.

## 15. Source integrity gates
Before applying missing-row or mass-change decisions, validate:
- source was fully readable
- expected sheet/range exists
- header structure is credible
- record count is plausible against recent baseline
- no abnormal parse truncation occurred
Large unexpected drops should be flagged for review rather than treated as mass deletion.

## 16. Suspicious change protection
Examples:
- price jump beyond configured threshold
- technical field changes on many products simultaneously
- 90% of inventory disappears
- all rows suddenly map to a new field
Such cases enter a guarded state or warning workflow instead of blindly publishing.

## 17. Diff model
A Sync Diff summarizes:
- NEW
- CHANGED
- UNCHANGED
- MISSING
- AMBIGUOUS
- BLOCKED
- INVALID
Each changed product includes changed fields and risk classification.

## 18. Error isolation
Row-level errors must not fail unrelated rows. The sync can complete partially with explicit counts. System-level source corruption can block the entire destructive portion while retaining diagnostics.

## 19. Google Sheets efficiency
For Google Sheets, prefer batched reads where practical. Google officially supports values.get/batchGet and recommends batch methods to reduce requests. Developer metadata can also associate metadata with rows/cells. citeturn926801search0turn926801search2turn926801search3
V1 should read only configured ranges/columns rather than the entire spreadsheet when possible.

## 20. Customer-managed product media
Product media may originate from source data or be managed from the application. Application-managed media must have independent stable identity and not be destroyed merely because a source image column changes.

## 21. Sync concurrency
At most one authoritative sync per Source should mutate product state at a time. Duplicate trigger requests should coalesce or become no-op/idempotent jobs.

## 22. Subscription limits
A sync must verify entitlement before expensive work. Limits include product count, source count, frequency, and monthly operations as configured by the subscription system.

## 23. Recovery
After worker/server crash, a job is recovered according to its state and idempotency key. Partially applied product changes must be reconciled before retrying downstream publication.

## 24. Acceptance tests
Must include row reorder, insertion, deletion, recreation, duplicate rows, no IDs, changed SKU, renamed columns, changed mapping, malformed values, permission loss, partial read, mass disappearance, suspicious change, manual sync duplicate, scheduled/manual race, worker crash and recovery.
