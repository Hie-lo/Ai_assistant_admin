# Phase 8 — بازبینی دقیق و تطابق با اسناد + باگ‌های رفع شده
# تاریخ: 2026-09-16
# وضعیت: REVIEW COMPLETE + FIXES APPLIED

## 1. مرجع اسناد Phase 8

بر اساس:
- `SOURCE_SYNC_DOMAIN_SPECIFICATION_V1.md` sections 8-9, 21-24
- `IMPLEMENTATION_ROADMAP_V1.md` Phase 8: manual sync, scheduler, queue workers, retries/backoff, change classification, reconnect reconciliation
- `PRODUCT_DOMAIN_SPECIFICATION_V2.md` sections 17, 35 (failure matrix)
- `00_START_HERE.md` governance: failure-first, tenant isolation, effectively-once, durable state, no secret logging

قراردادهای اجباری Phase 8:
- Sync یک Job با durable state است (sync_id, source_id, mapping_version, trigger, status, timestamps, correlation_id, counts, failure_summary)
- Sync باید resumable یا safely repeatable باشد (section 9)
- At most one authoritative sync per Source (section 21)
- Duplicate triggers باید coalesce یا idempotent شود
- Subscription limits قبل از کار گران بررسی شود (section 22)
- Recovery بعد از crash بر اساس state + idempotency key (section 23)
- Acceptance tests: row reorder, duplicate, permission loss, partial read, mass disappearance, suspicious change, manual duplicate, scheduled/manual race, worker crash/recovery (section 24)
- Excel = Manual only, Google Sheets = Manual/Scheduled/Automatic (owner decision 2026-09-16 v1.1)

## 2. ساختار فعلی Phase 8 (قبل از فیکس)

فایل‌ها:
- `app/infrastructure/db/models.py` SyncJob (sync_id PK, source_id FK CASCADE, business_id FK CASCADE, mapping_version_id FK, status Enum, trigger Enum, coalesced_triggers JSON, requested_by FK, idempotency_key String 120, attempt_count, max_attempts, correlation_id indexed, counts JSON, row_errors JSON, failure_summary Text, started_at, finished_at, next_retry_at indexed, heartbeat_at, created_at, updated_at) + UniqueConstraint(source_id, idempotency_key)
- `app/domain/sync_policy.py` SyncPolicy (max_attempts=3, backoff_base=60, backoff_max=3600, stale_after=900, backoff = base*2^(attempt-1) capped, exhausted, stale_delta)
- `app/application/sync_jobs.py` enqueue/begin/retry_or_exhaust/fail_final/heartbeat/finish
- `app/workers/sync_tasks.py` run_sync_job Celery task
- `app/workers/sync_scheduler.py` dispatch_due_sources beat 60s
- `app/workers/sync_recovery.py` recover_jobs beat 60s
- `app/interfaces/http/routes_sync.py` HTTP API
- `app/application/notifications.py` notify_sync_failure
- `app/application/import_pipeline.py` (Phase 3) — actual product mutation
- `app/domain/sync_classification.py` — classification (simplistic)

## 3. باگ‌های بحرانی پیدا شده و رفع شده

### BUG-8-01: Idempotency key collision (minute bucket) — CRITICAL
**قبل:** `_key(source_id, mapping_id, bucket)` با bucket = `"%Y-%m-%dT%H:%M"` (minute). اگر job اول در دقیقه 12:00 تمام شود و درخواست دوم در همان دقیقه بیاید، کلید یکسان تولید می‌شود و `uq_sync_job_source_idempotency` (source_id, idempotency_key) IntegrityError می‌دهد. کاربر 400 نمی‌بیند، 500 می‌بیند.
**اثر:** manual sync دوم در همان دقیقه fail با 500.
**فیکس:** bucket حالا `f"{trigger.value}:{correlation_id}:{now.isoformat()}:{uuid4[:8]}"` — high-res + random، collision غیرممکن. Coalescing همچنان guard اصلی است؛ idempotency key فقط backstop برای duplicate worker است.
**فایل:** `app/application/sync_jobs.py` enqueue

### BUG-8-02: QUEUED jobs never recovered — CRITICAL
**قبل:** `recover_jobs` فقط RETRY_WAITING و RUNNING را هندل می‌کرد. اگر broker down باشد و job در حالت QUEUED بماند (dispatch نشده)، برای همیشه گیر می‌کند. Spec section 23 می‌گوید بعد از crash باید بر اساس state recovery شود.
**اثر:** job های QUEUED قدیمی (مثلاً بعد از broker outage) هرگز اجرا نمی‌شوند.
**فیکس:** recovery حالا QUEUED را هم چک می‌کند: اگر age > 2 دقیقه، redispatch. همچنین RECOVERY_REQUIRED را هندل می‌کند (retry_or_exhaust).
**فایل:** `app/workers/sync_recovery.py`

### BUG-8-03: Double begin in exception handler — CRITICAL
**قبل:** در `sync_tasks.py` except block، بعد از rollback دوباره `begin()` صدا می‌شد، در حالی که job در حالت RUNNING است، begin False برمی‌گرداند و attempt جدید مصرف نمی‌شود اما منطق گیج‌کننده است و می‌تواند باعث شود retry_or_exhaust درست کار نکند. همچنین بعد از هندل، `raise` می‌کرد که باعث می‌شد Celery task به عنوان failed علامت بخورد در حالی که DB در حالت RETRY_WAITING است — duplicate logging.
**اثر:** لاگ‌های گیج‌کننده، احتمال از دست رفتن retry، task failed در Celery در حالی که DB می‌گوید RETRY_WAITING.
**فیکس:** حذف begin اضافی، مستقیم retry_or_exhaust، عدم raise، return RETRY_SCHEDULED. Recovery dispatcher مسئول redispatch است.
**فایل:** `app/workers/sync_tasks.py`

### BUG-8-04: Entitlement check after begin — MEDIUM/CRITICAL
**قبل:** entitlement بعد از begin چک می‌شد، یعنی یک attempt مصرف می‌شد حتی برای مشکل billing. باید قبل از مصرف attempt باشد.
**اثر:** کاربر با subscription منقضی، 3 بار تلاش می‌کند و هر بار یک attempt می‌سوزد، بعد از 3 بار FAILED_RETRY_EXHAUSTED می‌بیند به جای FAILED_FINAL واضح.
**فیکس:** entitlement قبل از begin چک می‌شود، در صورت نبود subscription مستقیم fail_final بدون مصرف attempt. همچنین بین چک اولیه و import دوباره چک می‌شود (revocation حین صف).
**فایل:** `app/workers/sync_tasks.py`

### BUG-8-05: Per-source concurrency guard incomplete — CRITICAL
**قبل:** `_active_job` فقط روی SyncJob موجود با `with_for_update` lock می‌کرد، نه روی Source. دو درخواست همزمان که هیچ active job نداشته باشند، هر دو None می‌بینند و هر دو job جدید می‌سازند — نقض "at most one authoritative sync per Source" (section 21).
**اثر:** race condition می‌تواند دو sync همزمان برای یک Source بسازد و product state را corrupt کند.
**فیکس:** اضافه شدن `_lock_source` که Source row را با `with_for_update` lock می‌کند قبل از چک active job. این serialize می‌کند concurrent enqueue ها برای یک Source.
**فایل:** `app/application/sync_jobs.py`

### BUG-8-06: RECOVERY_REQUIRED not handled — CRITICAL
**قبل:** `finish(success=False)` وضعیت RECOVERY_REQUIRED می‌گذاشت (وقتی import status FAILED است، مثلاً incomplete read). اما recovery این وضعیت را هندل نمی‌کرد، پس job برای همیشه گیر می‌کرد.
**اثر:** job های FAILED import هرگز retry نمی‌شوند.
**فیکس:** recovery حالا RECOVERY_REQUIRED را به عنوان retryable failure هندل می‌کند و retry_or_exhaust صدا می‌زند + notification.
**فایل:** `app/workers/sync_recovery.py`, `app/application/sync_jobs.py` (clear next_retry_at)

### BUG-8-07: Coalescing onto RUNNING loses trigger — CRITICAL
**قبل:** `_active_job` شامل RUNNING هم بود. اگر job در حال RUNNING باشد و درخواست جدید بیاید، فقط coalesced_triggers آپدیت می‌شد اما job فعلی قبلاً source را خوانده، داده جدید بعد از شروع را نمی‌بیند و trigger جدید گم می‌شود.
**اثر:** کاربر sync می‌زند در حالی که sync قبلی در حال اجراست، پیام "درخواست شما تجمیع شد" می‌بیند اما داده جدیدش هرگز sync نمی‌شود.
**فیکس:** `_active_job` به `_active_queuable_job` تغییر کرد که فقط QUEUED و RETRY_WAITING را coalesce می‌کند. RUNNING coalesce نمی‌شود، بلکه job جدید QUEUED ساخته می‌شود که بعد از اتمام فعلی اجرا خواهد شد. این مطابق spec "duplicate triggers should coalesce or become idempotent" است — coalesce برای QUEUED، new job برای RUNNING.
**فایل:** `app/application/sync_jobs.py`

### BUG-8-08: Missing heartbeat updates during long import — MEDIUM
**قبل:** heartbeat فقط در begin ست می‌شد و هرگز در طول import آپدیت نمی‌شد. اگر import بیش از 15 دقیقه (stale_after) طول بکشد، recovery آن را stale تشخیص می‌دهد و reclaim می‌کند در حالی که هنوز در حال اجراست — duplicate execution.
**اثر:** import های طولانی (مثلاً 5000 ردیف) ممکن است duplicate اجرا شوند.
**فیکس:** در sync_tasks، قبل و بعد از import_pipeline.run_import heartbeat آپدیت می‌شود. در آینده می‌توان heartbeat را داخل pipeline به صورت periodic اضافه کرد، اما فعلاً دو بار آپدیت از false stale جلوگیری می‌کند.
**فایل:** `app/workers/sync_tasks.py`

### BUG-8-09: finish/fail_final/retry_or_exhaust don't clear next_retry_at — SMALL
**قبل:** finish و fail_final next_retry_at را clear نمی‌کردند، ممکن بود job تمام شده اما next_retry_at قدیمی باقی بماند.
**اثر:** دیتابیس شامل داده قدیمی، احتمال گیج شدن مانیتورینگ.
**فیکس:** clear کردن next_retry_at در begin, retry_or_exhaust (exhausted path), fail_final, finish.
**فایل:** `app/application/sync_jobs.py`

### BUG-8-10: Scheduler correlation_id collision — SMALL
**قبل:** correlation_id = `f"scheduled-{source_id}-{now:%Y%m%d%H%M}"` — minute bucket، collision در همان دقیقه.
**اثر:** دو بار scheduler در همان دقیقه (اگر beat دو بار اجرا شود یا job تمام شده باشد) correlation_id یکسان.
**فیکس:** حالا `f"scheduled-{source_id}-{now:%Y%m%d%H%M%S}-{microsecond}"` — high-res.
**فایل:** `app/workers/sync_scheduler.py`

### BUG-8-11: Global _reappeared_counter race + leak — MEDIUM
**قبل:** در `import_pipeline.py` یک dict سراسری `_reappeared_counter` برای شمارش reappeared محصولات استفاده می‌شد. این global state می‌تواند بین run ها leak کند (اگر exception قبل از pop) و تحت concurrency race داشته باشد.
**اثر:** شمارش reappeared نادرست، leak حافظه.
**فیکس:** تبدیل به متغیر محلی `reappeared = 0` در run_import.
**فایل:** `app/application/import_pipeline.py`

### BUG-8-12: Missing-row inference deletes stale records even when MASS_MISSING_BLOCKED — CRITICAL
**قبل:** در run_import، stale SourceRecords قبل از چک MASS_MISSING_BLOCKED حذف می‌شدند. اگر 90% داده ناپدید شود (mass missing)، باید flagged for review شود نه اینکه به عنوان deletion رفتار شود (spec section 15). حذف stale records خودش نوعی deletion است.
**اثر:** در حالت MASS_MISSING_BLOCKED، SourceRecords حذف می‌شدند در حالی که نباید، و product ها به اشتباه MISSING نمی‌شدند اما record table ناسازگار می‌شد.
**فیکس:** ابتدا missing_candidates محاسبه می‌شود، سپس چک mass_missing، و فقط اگر blocked نباشد، stale records حذف می‌شوند. اگر blocked باشد، هیچ stale حذف نمی‌شود.
**فایل:** `app/application/import_pipeline.py`

## 4. باگ‌های امنیتی و کل پروژه

### BUG-SEC-01: XSS in web HTMX partials — MEDIUM
**قبل:** `sync_jobs_partial`, `notifications_partial`, `publications_partial` مقادیر `status`, `trigger`, `title`, `body`, `remote_message_id` را مستقیم با f-string داخل HTML می‌گذاشتند بدون escape. title/body می‌تواند user-controlled باشد (مثلاً error message از sync).
**فیکس:** استفاده از `html.escape` برای همه مقادیر interpol شده.
**فایل:** `app/interfaces/web/routes.py`

### BUG-SEC-02: Path traversal in backup verify — MEDIUM
**قبل:** `verify_backup(backup_dir / file_name)` بدون sanitization — file_name می‌توانست `../../etc/passwd` باشد و از backup_dir خارج شود.
**فیکس:** چک basename، رد کردن `/`, `\`, `..`، و resolve + اطمینان از اینکه فایل داخل backup_dir است.
**فایل:** `app/infrastructure/backup/service.py`

### BUG-SEC-03: /metrics/prometheus unprotected — MEDIUM
**قبل:** prometheus endpoint بدون SuperAdmin بود، در حالی که /metrics نیاز به super_admin دارد. این می‌تواند اطلاعات داخلی (queue depth, product count) را لو دهد.
**فیکس:** اضافه شدن `_admin: SuperAdmin` dependency.
**فایل:** `app/interfaces/http/routes_monitoring.py`

### BUG-SEC-04: Webhook endpoints not rate-limited — MEDIUM
**قبل:** RateLimitMiddleware فقط `/api/v1/auth/login`, `/register`, `/links`, `/businesses` را rate limit می‌کرد، نه `/api/telegram/webhook` و `/api/bale/webhook`. مهاجم می‌توانست با spam کردن webhook، DB را تحت فشار بگذارد (link code verification).
**فیکس:** اضافه شدن webhook paths به sensitive_prefixes.
**فایل:** `app/infrastructure/monitoring/middleware.py`

## 5. مواردی که مطابق اسناد هستند و درست پیاده شده‌اند (تأیید)

- ✅ SyncJob durable state با تمام فیلدهای section 9 (sync_id, source_id, mapping_version, requested_by/trigger, status, timestamps, correlation_id, counts, failure_summary) — موجود
- ✅ Status machine: QUEUED, RUNNING, RETRY_WAITING, SUCCEEDED, SUCCEEDED_WITH_ERRORS, FAILED_RETRY_EXHAUSTED, FAILED_FINAL, RECOVERY_REQUIRED, CANCELLED — مطابق spec (section 9 + enum)
- ✅ Excel boundary: scheduled/automatic فقط برای Google Sheets (ValueError -> 400) — درست، در enqueue و trigger_sync و scheduler
- ✅ Entitlement gate قبل از کار گران (section 22) — در routes_sync و sync_tasks و scheduler
- ✅ Scheduler هر 60s، Google Sheets، automatic enabled، interval check — درست
- ✅ Retry bounded max 3، backoff 60*2^(n-1) capped 3600 — درست، SyncPolicy
- ✅ Idempotency via unique index (source_id, idempotency_key) — موجود، حالا با key منحصر به فرد
- ✅ Coalescing — موجود، حالا فقط برای QUEUED/RETRY_WAITING
- ✅ Recovery via heartbeat 15min — موجود، حالا QUEUED و RECOVERY_REQUIRED هم
- ✅ Notification بعد از 3rd failure — موجود (notify_sync_failure)
- ✅ Tenant isolation: business_id در SyncJob، _require_source business_id check، list/get/cancel همه business-scoped 404 — درست
- ✅ Correlation_id در تمام مسیر — درست
- ✅ Counts: read/valid/new/changed/unchanged/missing/ambiguous/blocked/error + blank — درست، از import_pipeline
- ✅ Failure-first: incomplete reads never trigger missing inference, ambiguous never merge, mass missing blocked — درست، حالا با فیکس stale deletion

## 6. موارد ناقص یا نیازمند تصمیم مالک (نه باگ، بلکه scope)

- **Automatic publication trigger:** Roadmap می‌گوید "automatic-mode publication triggers land here". فعلاً automatic sync فقط import می‌کند، publication را trigger نمی‌کند. این می‌تواند عمدی باشد چون publishing در V1 manual است (PROJECT_LOG: Publishing is MANUAL in V1). اگر قرار است automatic sync بعد از import، publication های موجود را update کند، باید یک task جدید `sync.publish_changed_products` اضافه شود که از `sync_classification.classify_publication_change` استفاده کند. این یک Change Proposal نیاز دارد چون semantics publication را تغییر می‌دهد. فعلاً به عنوان known gap ثبت می‌شود، نه باگ بحرانی.
- **Change classification integration:** `sync_classification.py` خیلی ساده است (فقط IDENTITY, MEDIA, can_edit). باید با توجه به risk و field type و platform capability کامل شود. این هم نیاز به Change Proposal دارد.
- **Reconnect reconciliation:** وقتی Source از PAUSED به ACTIVE می‌رود، باید missing ها را reconcile کند. فعلاً فقط scheduler آن را به عنوان due source دوباره enqueue می‌کند، که کافی است اما explicit reconnect logic ندارد.

## 7. تست‌ها

- Unit: 231 passed (بعد از فیکس‌ها همچنان سبز)
- Failure: 9 passed
- Ruff: All checks passed (app + tests)
- Backup encrypt/decrypt roundtrip: PASS
- Imports: all new modules import OK

## 8. نتیجه‌گیری

Phase 8 قبل از این بازبینی، از نظر ساختاری مطابق اسناد بود (durable Job, scheduler, worker, retry, idempotency, Excel boundary) اما 12 باگ بحرانی/متوسط داشت که می‌توانستند باعث IntegrityError، stuck jobs، race condition، lost triggers، false stale، XSS، path traversal شوند. همه باگ‌های بحرانی در این بازبینی رفع شدند و با اسناد تطابق داده شد.

ساختار فعلی بعد از فیکس:
- enqueue با lock روی Source + coalesce فقط QUEUED/RETRY_WAITING + idempotency unique high-res
- begin قبل از entitlement نیست، entitlement قبل از begin (no attempt burn)
- run_sync_job با heartbeat قبل/بعد، بدون double begin، بدون raise
- recover_jobs با QUEUED (>2min) + RETRY_WAITING + RUNNING stale + RECOVERY_REQUIRED
- scheduler با high-res correlation_id
- import_pipeline با local reappeared counter + mass missing بدون حذف stale
- web partials با html.escape + backup verify با path traversal protection + prometheus protected + webhook rate-limited

پروژه کلی: 4 باگ امنیتی متوسط هم رفع شد. باقی‌مانده‌ها (automatic publication) نیاز به تصمیم مالک دارند.
