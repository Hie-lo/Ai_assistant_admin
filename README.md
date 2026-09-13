# دستیار هوشمند کسب‌وکارهای مجازی

Multi-tenant SaaS برای وارد کردن محصولات از Excel/Google Sheets، ساخت مدل
داخلی پایدار، رندر پست با Preset + AI اختیاری، انتشار و همگام‌سازی در
پلتفرم‌های پیام‌رسانی (Telegram، Bale، Eitaa، Rubika) و مدیریت از طریق
پنل‌های Web/Telegram/Bale.

## وضعیت فعلی

- **Phase 0 — Approval & baseline: انجام شد** ✅
  - Gates A–H توسط Project Owner تأیید شدند (مشاهده: `docs/PROJECT_LOG.md`)
  - پکیج ۲۲ سندی طراحی در `docs/` موجود و validate شده است
  - اسکلت پروژه + CI + Alembic + Docker Compose + تست‌های structural آماده
- **Phase 1 — Auth / User / Business / Membership / RBAC: در نوبت**
  - طرح فازی برای ارائه به مالک و سپس پیاده‌سازی
- طبق قرارداد Bootstrap (`docs/00_START_HERE.md`)، همه‌ی تغییرات بنیادین
  پیش از اجرا به مالک پیشنهاد و برای تأیید ارسال می‌شود.

## ساختار ریپو

```
app/            # لایه‌ها: domain / application / infrastructure / interfaces / workers / config
tests/          # unit / integration / contract / e2e / failure
migrations/     # Alembic (schema migrations)
docs/           # ۲۲ سند رسمی طراحی + گزارش‌ها + runbooks
scripts/        # اسکریپت‌های عملیاتی (backup/restore در فازهای بعد)
deploy/         # nginx / compose overrides / runbook
templates/      # Jinja2 (Web UI — فاز 9)
static/         # دارایی‌های استاتیک
backups/        # خروجی بکاپ محلی (git-ignored)
```

## توسعه‌ی محلی

```bash
cp .env.example .env
make install        # نصب وابستگی‌ها (Python 3.13)
make lint           # ruff
make test           # تست‌های واحد (بدون سرویس)
make compose-up     # postgres + redis + web + worker
make migrate-up     # اعمال migrations
```

- Health: `GET /healthz` (liveness) و `GET /readyz` (readiness + بررسی DB)
- CI: `.github/workflows/ci.yml` — lint + unit (Python 3.13) و integration
  (PostgreSQL 17 + Redis 7 به‌عنوان service)

## اسناد و governance

- **سند ورود اجباری:** `docs/00_START_HERE.md` (قرارداد تعامل AI Developer)
- **گزارش فهم پروژه:** `docs/PROJECT_UNDERSTANDING_REPORT_V1.md`
- **Log و تصمیمات:** `docs/PROJECT_LOG.md` و `docs/PROJECT_CHANGELOG_AND_DECISIONS.md`
- ترتیب خواندن ۲۲ سند: مشاهده `docs/PROJECT_PACKAGE_README.md`

### استک (Gate F — تأییدشده)
Python 3.13 · FastAPI · SQLAlchemy 2.0.x · Alembic · PostgreSQL (هدف 18،
fallback 17) · Redis · Celery · Jinja2 + HTMX · Docker Compose
