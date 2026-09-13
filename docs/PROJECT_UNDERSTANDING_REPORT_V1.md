# PROJECT UNDERSTANDING REPORT v1
## دستیار هوشمند کسب‌وکارهای مجازی

- تاریخ: 2026-09-13
- تهیه‌کننده: AI Developer پروژه
- وضعیت: ارائه شده به مالک برای بررسی و تأیید
- مرجع: پکیج کامل طراحی (۲۲ سند، تاریخ 2026-09-12) مطابق قرارداد Bootstrap در `00_START_HERE.md`

---

## ۱. خلاصه فهم پروژه

این پروژه یک **SaaS چندباجه (multi-tenant)** است که هدف اصلی‌اش **انتشار و همگام‌سازی قابل‌اعتماد محصولات** کسب‌وکارهای مجازی است — نه چت‌بات عمومی. زنجیره ارزش:

1. **Ingestion**: خواندن محصولات از Excel/XLSX یا Google Sheets؛ ساختار ستون‌ها دلخواه، با پیشنهاد mapping + تأیید/اصلاح مشتری.
2. **Normalization**: تبدیل به مدل داخلی پایدار (canonical Product) با `product_id` ناگهیر (immutable) که **مستقل از شماره/ترتیب ردیف** شیت است؛ حل هویت بر پایه سلسله‌مراتب شواهد؛ **عدم auto-merge** در حالت مبهم.
3. **Content**: Presetهای ساختاریافته و نسخه‌دار (با Preset پیش‌فرض برای هر Business Type) + **AI اختیاری** (ai_description، ai_recommendation و…) به‌صورت خروجی‌های ساختاریافته، نسخه‌دار، قابل ویرایش دستی، قابل تأیید و قابل استفاده مجدد — AI هرگز منبع حقیقت قیمت/موجودی/مشخصات نیست.
4. **Publication**: رندر مستقل برای هر پلتفرم از طریق **adapter** با capability matrix صریح (Telegram، Bale؛ سپس Eitaa، Rubika بعد از certification)؛ ماشین حالت صریح `Post → PostVersion → Publication → PublicationAttempt`؛ idempotency + concurrency guard + reconciliation؛ هدف **effectively-once** (فرض exactly-once ممنوع).
5. **Sync**: همگام‌سازی دستی + زمان‌شده؛ تشخیص و طبقه‌بندی تغییرات (قیمت/موجودی/مشخصات/میدیا)؛ به‌روزرسانی یا repost با توالی امن **Publish New → Verify → Delete Old** یا حذف، بر اساس capability پلتفرم.
6. **مدیریت بیزینس**: ثبت‌نام/ورود عمومی، Business به‌عنوان tenant، Owner/Admin (Admin فقط با درخواست + تأیید Owner)، RBAC با دانه‌بندی ظریف و scope روی Business، اشتراک/پرداخت/entitlement، audit trail.
7. **Operations**: لاگ ساختاریافته با Correlation ID (پاسخ به WHO/TENANT/BUSINESS/…/RESULT)، metrics + alerts عملی‌پسند، health (liveness/readiness جدا)، **بکاپ encrypted + off-site + Restore Test اجباری**.

فلسفه غالب همه اسناد: **Failure-first** — قبل از کدنویسی، رفتار عادی، ناکامی، تکرار، timeout، restart، از دست رفتن permission و recovery برای هر قابلیت تعریف می‌شود.

## ۲. V1 Scope (خلاصه)

- ثبت‌نام/احراز هویت عمومی؛ ساخت Business و اصلاح/تغییر بعدی آن
- اشتراک‌ها، جریان پرداخت (behind adapter)، entitlements قابل نفاذ
- Owner + مدیران (Admin) تأییدشده توسط Owner
- اهداف پلتفرمی: Telegram، Bale، Eitaa، Rubika — **مشمول certification**
- سه سطح مدیریت با یک backend مشترک: Web Panel، Telegram Panel، Bale Panel
- منابع: Excel/XLSX + Google Sheets؛ template نمونه رسمی برای هر Business Type
- mapping انعطاف‌پذیر + تأیید مشتری + mapping profileهای نسخه‌دار و قابل استفاده مجدد
- Product/ProductVersion + Media به‌عنوان جزء یک‌درجه‌ای + فیلدهای سفارشی typed
- Preset/Template نسخه‌دار + AI اختیاری (دستی + خودکار با retry محدود) + Preview
- انتشار/ویرایش/بازانتشار/حذف بر اساس capability پلتفرم
- Sync دستی + زمان‌شده، رهگیری پایدار انتشار، idempotency/concurrency/reconciliation
- مانیتورینگ، لاگ ساختاریافته، alert، audit
- بکاپ خودکار encrypted + DR + restore
- آموزش/تورنال و قابلیت‌های رایج SaaS (پروفایل، اعلان، support، maintenance mode، feature flags، locale/timezone، export امن داده، نسخه‌بندی terms/privacy)

## ۳. Non-Goals (خارج از هسته V1)

- CRM کامل
- ایجنت فروش/پشتیبانی خودکار
- تصمیم‌گیری خودکار بیزینسی
- مارکت‌پلیس جذب Admin
- مشاوره پیشرفته بیزینسی به‌عنوان وابستگی
- ویرایش محصول از داخل بات
- Instagram و Website تا زمانی که جداگانه certification شوند

## ۴. Domainها و رابطه آن‌ها

```text
User ←(Membership: OWNER/ADMIN)→ Business
Business → Source →(SourceMapping versioned)→ Product → ProductVersion
Product → ProductMedia (first-class)
Product → Post → PostVersion (structured blocks + fingerprint)
PostVersion → Publication (per platform/connection) → PublicationAttempt(s)
Preset (versioned) + AI Output Artifacts (versioned) → تغذیه PostVersion
Subscription → Entitlements → درِ ورود عملیات‌های گران/حساس
Job/Queue (Sync, Publish, AI) → اجرای همه‌ی side effects خارجی
Audit + Notification → همراه همه‌ی عملیات حساس
```

رابطه‌های کلیدی (اینواریانت‌ها):
- **Product ≠ Post ≠ Publication** — هر سه هویت مجزا دارند.
- شماره/ترتیب ردیف **هرگز** هویت محصول نیست.
- نسخه‌های تاریخی Preset/AI/Mapping **هرگز** بازنویسی نمی‌شوند؛ تاریخ به همان نسخه‌ای که مصرف کرده پیوسته می‌ماند.
- یک Product می‌تواند Publicationهای متعددی در پلتفرم/کانال‌های مختلف داشته باشد.
- اشتراک/entitlement و authorization دو لایه مجزا هستند: `effective = role ∩ business policy ∩ entitlement ∩ resource scope`.

## ۵. قوانین امنیتی مهم (انتخاب)

1. **ایزولاسیون tenant**: هر مسیر دسترسی به داده scope روی Business/Tenant است؛ تست حمله‌های cross-tenant اجباری است.
2. **Backend enforce**: UI هرگز مرز امنیتی نیست؛ همه‌ی authorization server-side.
3. **لینک اکانت**: اتصال Web/Telegram/Bale فقط با مکانیزم one-time + انقضاشونده؛ تطبیق نام کاربری هرگز مدرک هویت نیست.
4. **Channel control verification**: اثبات کنترل فنی روی کانال (permissions + اتصال) — متفاوت از مالکیت حقوقی؛ label: CONTROL VERIFIED.
5. **Secrets**: اعتبارنامه‌ها (bot token، Google OAuth، payment) encrypted در ذخیره‌سازی؛ **هرگز** در لاگ.
6. **Template safety**: گرامر allowlist؛ اجرای کد از template یا فیلدهای منبع ممنوع.
7. **SSRF protection**: محدودسازی scheme/IP خصوصی/redirect برای fetch URL تصویر/منبع خارجی.
8. **Audit**: actor، business، target، نتیجه، correlation ID برای همه‌ی عملیات حساس (ورود، لینک، نقش، اتصال، اشتراک، عملیات تخریبی).
9. **مدیریت Admin**: درخواست → تأیید Owner؛ خودتأیید ممنوع؛ revocation فوری + invalidate session + re-evaluate صف؛ audit کامل.
10. **Abuse protection**: rate limit روی login/linking/import/AI/sync/publish.

## ۶. قوانین Failure/Recovery مهم (انتخاب)

1. **Unknown remote outcome حالت یک‌درجه‌ای است**: timeout پس از ارسال ممکن‌شده = `UNKNOWN_REMOTE_STATE`؛ reconcile قبل از هر retry؛ retry کور ممنوع.
2. **Missing source ≠ حذف**: ردیف گم‌شده → `MISSING_FROM_SOURCE` (غیرتخریبی)؛ اگر read ناقص/ممنوعه/شبهه‌دار بوده، استنتاج missing اصلاً اجرا **نمی‌شود**؛ mass deletion فقط با policy صریح + اعتماد کافی.
3. **Ambiguous identity → review**: هرگز auto-merge؛ false merge خطرناک‌تر از false-new-product است.
4. **Retry محدود**: backoff + سقف تلاش؛ infinite loop ممنوع؛ شکست‌های validation/final به manual fallback.
5. **توالی امن repost**: Publish New → Verify → Delete Old (هرگز حذف اول).
6. **Reconnect ≠ bulk republish**: فقط ادامه‌ی کارهای باقی‌مانده بر اساس رکوردهای پایدار.
7. **Crash/restart**: jobها idempotent؛ بازیابی از DB (منبع حقیقت)؛ cache disposable.
8. **Epic isolation**: ناکامی یک پلتفرم/tenant/AI/source، حالت بقیه را خراب نمی‌کند.
9. **Backup بدون Restore Test معتبر نیست**.
10. **Subscription expiry**: داده‌ها سالم می‌مانند؛ jobهای صف قبل از side effect خارجی دوباره re-evaluate می‌شوند.

## ۷. تصمیمات Approved (ثبت‌شده در Changelog، 2026-09-12)

- Failure-first principle برای همه‌ی قابلیت‌های بزرگ
- جداسازی هویت Product از ردیف/SKU؛ ID داخلی ناگهیر
- Source safety: missing row ≠ حذف خودکار؛ read ناقص ≠ mass removal
- effectively-once برای عملیات خارجی (نه exactly-once)
- AI اختیاری/غیرسازنده؛ reuse خروجی تأییدشده؛ **بدون** regenerate خودکار پست‌های تاریخی بعد از تغییر prompt/model
- AI outputs configuration-driven و نسخه‌دار
- Custom fields typed + tokenهای template کنترل‌شده (`{touch}` → دارد/ندارد)
- نسخه‌بندی Preset؛ تغییر Preset، تاریخ/صف را بازنویسی نمی‌کند
- Platform isolation: adapter + capability matrix مستقل؛ generalization از Telegram ممنوع
- Eitaa/Bale/Rubika: فقط بعد از certification (Telegram parity فرض نمی‌شود)
- بکاپ: encrypted + local rotation + off-site اجباری + Restore Test؛ Telegram فقط کپی تکمیلی
- درس‌آموخته‌های پروژه‌ی قبلی: فقط به‌عنوان درس/blind-spot؛ کد/سکیم/استک/حدود آن پروژه مرجع نیست
- وضعیت فعلی ثبت‌شده: «هیچ schema/استک/معماری بنیادین بدون تأیید صریح مالک تصویب نشده است»

## ۸. تصمیمات Pending Approval (Gates)

| Gate | موضوع | پیشنهاد اسناد |
|------|-------|---------------|
| A | Domain contracts (Product v2 + Post/Publication v1) | تأیید قراردادها |
| B | Policy دقیق Identity Resolution + آستانه‌ها + رفتار ambiguity | سلسله‌مراتب شواهد ثبت‌شده |
| C | سیاست Missing Source اولیه | قرنطینه امن / بررسی دستی (auto-unpublish پیش‌فرض نیست) |
| D | مدل Owner/Admin + permission matrix پیش‌فرض | همان که در RBAC spec است |
| E | ترتیب فعال‌سازی پلتفرم‌ها | اول Telegram + Bale؛ Eitaa و Rubika مستقل و بعد از certification |
| F | استک (Python 3.13/FastAPI/SQLAlchemy 2.0.x/PostgreSQL/Redis/Celery/Jinja2+HTMX/Docker) | تصویب پیشنهادی + قفل نسخه‌ها بعد از تست سازگاری |
| G | حالت/ارائه‌دهنده‌ی پرداخت اولیه | manual verification قابل‌قبول در V1؛ درگاه پشت adapter |
| H | استراتژی بکاپ | encrypted local rotation + off-site + optional Telegram copy + restore test |
| I | راه‌اندازی Production | فقط بعد از گذر certification + security review + load + restore + failure-matrix |

همه‌ی Gates بالا **هنوز تصویب نشده‌اند**.

## ۹. تناقض‌های احتمالی بین اسناد

1. **جایگاه Subscription در ترتیب پیاده‌سازی**: `IMPLEMENTATION_ROADMAP_V1.md` آن را فاز ۲ (پس از Auth، قبل از Source/Product) می‌داند، اما ترتیب مرجع `00_START_HERE.md` (بخش ۲۱) آن را شماره ۱۲ (بعد از AI) نشان می‌دهد. **پیشنهاد این گزارش**: موتور entitlement به‌صورت حداقلی زود ساخته شود (چون عملیات‌های گران باید از همان ابتدا gate شوند) و ادغام درگاه پرداخت کامل در فاز بعدی — نیازمند تأیید مالک. (تناقض نرم؛ 00_START_HERE ترتیب مرجع 21 را قابل اصلاح اعلام کرده.)
2. **MASTER_PROJECT_SPECIFICATION** دو بخش با شماره «17» دارد (Common SaaS capabilities و Training/tutorials) — خطای تایپو شماره‌گذاری؛ بی‌اثر بر محتوا.
3. **لیست permissionها** در `PRODUCT_DOMAIN_SPECIFICATION_V2` (§26) و `RBAC_SPEC` (§12) کمی متفاوت است — RBAC spec مرجع اصلی است؛ یک‌سان‌سازی نهایی در فاز 1 (تصمیم جزئی، غیربنیادین).

هیچ‌کدام از موارد بالا BLOCKER فنی نیستند؛ ثبت شدند تا در فاز مربوطه با تصمیم صریح بسته شوند.

## ۱۰. اطلاعاتی که کم است (نیازمند پاسخ مالک)

1. **سرور میزبان**: مشخصات (CPU/RAM/Disk) و **محل جغرافیایی** (داخل ایران / خارج). روی دسترسی به پلتفرم‌های ایرانی (Bale/Eitaa/Rubika)، Google API و Providerهای AI اثر مستقیم دارد.
2. **پرداخت**: شاپرک/درگاه ایرانی؟ manual verification؟ (Gate G)
3. **مقصد off-site بکاپ**: S3؟ سرور دوم؟ (Gate H)
4. **Provider AI**: OpenAI/Gemini/سایر یا محلی؟ (هزینه + دسترسی از محل سرور)
5. **Business Typeهای اولیه**: لپ‌تاپ؟ پوشاک؟ چند Business Type در V1 و template نمونه‌هایشان؟
6. **متون Terms/Privacy**: برای صحنه‌ی consent (محتوا توسط مالک/وکیل تأمین می‌شود).
7. **Botها**: Botهای Telegram/Bale موجود داریم یا ساخت جدید؟
8. **دامنه**: دامنه‌ی نهایی وب‌سایت.
9. **نام ریپو**: نام ریپو `Ai_assistant_admin` است ولی پکیج، کل سیستم (نه فقط admin) را در «this repository» می‌خواهد — تأیید می‌کنید کل سیستم در همین ریپو پیاده شود؟

## ۱۱. ریسک‌های اصلی

| ریسک | شدت | مدیریت (بر اساس طراحی موجود) |
|------|-----|------------------------------|
| عدم قطعیت API عیتا/روبینکا | متوسط | Gate certification مستقل؛ capability‌های نامعلوم = false تا تست زنده |
| منابع محدود سرور اولیه | متوسط | بدون loop/process دائمی per-tenant؛ state فشرده؛ اندازه‌گیری واقعی در فاز 11 |
| ناپایداری/timeout APIهای خارجی | بالا | effectively-once + UNKNOWN state + reconciliation (طراحی‌شده) |
| پیچیدگی پرداخت بازار ایرانی | متوسط | Gate G + adapter؛ manual verification در V1 |
| رشد ناخواسته scope (CRM/ایجنت خودکار) | متوسط | Non-goals صریح در Master + Start Directive |
| RTL/Persian UI | کم | پیچیدگی ظاهری؛ با test پوشش |
| وابستگی به AI | کم | AI اختیاری/non-critical؛ deterministic logic اولویت دارد |

## ۱۲. ترتیب پیشنهادی Implementation

با توافقی بین Roadmap و ترتیب مرجع Bootstrap (و حل تناقض §9.1 به پیشنهاد این گزارش):

```text
Phase 0   Approval & baseline: تصویب Gates، freeze قراردادها، اسناد + CI skeleton + infra migrations/test
Phase 1   Auth / User / Business / Membership / RBAC + audit foundation
Phase 2   Subscription / Entitlement (حداقلی) + payment adapter behind interface
Phase 3   Source / Mapping / import pipeline / Product / Identity / Media
Phase 4   Content: Preset registry + rendering + AI output registry + manual AI + Preview
Phase 5   Telegram certification + Publication engine (state machine, idempotency, reconciliation)
Phase 6   Bale certification + publication
Phase 7   Eitaa / Rubika certification (فقط قابلیت‌های پاس‌کرده فعال می‌شوند)
Phase 8   Sync engine: scheduler, queue workers, retries/backoff, change classification, reconnect reconciliation
Phase 9   Interfaces: Web Panel + Telegram + Bale management (یک use-case layer مشترک)
Phase 10  Monitoring / Alerts / Backup / Restore / DR
Phase 11  Scale hardening: load test, tuning, tenant isolation audit, resource profile
Phase 12  Production readiness: security review, failure-matrix pass, restore pass, rollback rehearsal
```

## ۱۳. تست‌های حیاتی (نمونه — فهرست کامل در TEST_STRATEGY و failure matrixها)

- re-order / delete / re-create ردیف‌ها → هویت محصول ثابت بماند
- identity مبهم → review؛ **بدون** merge
- job/worker تکراری یا هم‌زمان → اثر دقیقاً یک‌بازه (effectively-once)
- timeout بعد از موفقیت remote → `UNKNOWN` → reconcile → بدون انتشار مجدد
- restart وسط batch → ادامه فقط از رکوردهای ناقص؛ بدون duplicate
- حذف/ویرایش دستی پیام remote → طبق سیاست (بدون tفسیر به‌عنوان ویرایش Product)
- capability نبودن edit در پلتفرم → مسیر repost صریح
- cross-tenant access / privilege escalation / revocation وسط job → fail closed + بلاک فوری
- subscription expiry وسط queue → re-evaluate قبل از side effect
- backup → **restore test** در محیط تمیز
- malformed spreadsheet/URL/template → قرنطینه row/عملیات بدون آلوده‌کردن بقیه

## ۱۴. آیا Repository آماده شروع است؟

- ریپوی `Hie-lo/Ai_assistant_admin` در لحظه بررسی فقط README داشت؛ بدون کد، بدون CI، بدون ساختار.
- مطابق قرارداد Bootstrap، **کدنویسی پایه هنوز شروع نشده** و نباید شروع شود تا Gates تصویب شوند.
- انجام‌شده در این گزارش: تهیه PROJECT UNDERSTANDING REPORT (خروجی شماره ۱).
- گام‌های باقی‌مانده Phase 0 (فوری بعد از تصویب Gates):
  1. انتقال ۲۲ سند پکیج به `docs/specification/` در ریپو
  2. ساختار دایره‌ها مطابق TECHNOLOGY_AND_REPO_SPEC (§13)
  3. CI skeleton (GitHub Actions: lint + test)
  4. infra برای migrations (Alembic) و test (pytest + fixtureها)

---

## نتیجه‌گیری نهایی این گزارش

```text
READY_TO_PLAN        = YES
APPROVALS_REQUIRED   = [Gate A, Gate B, Gate C, Gate D, Gate E, Gate F, Gate G, Gate H]
BLOCKERS             = [هیچ BLOCKER فنی؛ موارد اطلاعاتی بخش ۱۰ در حال جمع‌آوری]
FIRST_PHASE          = Phase 0 (Approval & baseline) — شروع بعد از تأیید مالک
```

**اقدام بعدی امن** (مطابق FINAL_REVIEW_AND_SELF_SCORE_V1): دریافت تأیید مالک برای Gates، سپس پیاده‌سازی به ترتیب roadmap با تست و log بعد از هر تغییر بزرگ.
