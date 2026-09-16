"""Telegram management bot — business logic (Phase 9).

This module implements the Telegram-side management interface. It reuses
the same application/use-case layer as the Web panel (unified permission
layer). All state is durable in PostgreSQL; no business logic lives inside
the bot handlers (per TECHNOLOGY_AND_REPO_SPECIFICATION_V1 §14).

Flow (per USER_BUSINESS_MEMBERSHIP_RBAC_SPEC_V1 §16):
- User sends /start -> bot explains linking via one-time code from Web panel
- User sends a 6-digit code -> bot verifies via internal linking contract
- Once linked, user can run management commands: /businesses, /products,
  /sync, /notifications, /help

Security:
- Never trust username/name matching — linking requires proof via code
- Account linking is explicit, expiring, single-use (LinkCode model)
- Cross-tenant access fails closed; business scope resolved server-side
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application import business as biz_svc
from app.application import products as products_svc
from app.application import sources as sources_svc
from app.application import sync_jobs as sync_jobs_svc
from app.application.linking import verify_link_code
from app.application.mapping import active_mapping_for
from app.domain import enums
from app.infrastructure.db import models
from app.infrastructure.db.session import get_session_factory


@dataclass
class BotMessage:
    platform: str
    platform_user_id: str
    text: str
    chat_id: str | None = None
    username: str | None = None


@dataclass
class BotReply:
    text: str
    parse_mode: str | None = None


def _get_db() -> Session:
    factory = get_session_factory()
    return factory()


def _find_linked_user(db: Session, platform: str, platform_user_id: str):
    identity = db.scalar(
        select(models.AccountIdentity).where(
            models.AccountIdentity.platform == platform,
            models.AccountIdentity.platform_user_id == platform_user_id,
        )
    )
    if identity is None:
        return None
    return db.get(models.User, identity.user_id)


def _list_user_businesses(db: Session, user_id: uuid.UUID):
    user = db.get(models.User, user_id)
    if user is None:
        return []
    return biz_svc.list_businesses(db, user=user)


def handle_message(msg: BotMessage) -> BotReply:
    """Main entry point: handle one incoming message, return reply."""
    db = _get_db()
    try:
        text = (msg.text or "").strip()
        if not text:
            return BotReply(text="پیام خالی دریافت شد.")

        # Link code handling: 6-digit code
        if text.isdigit() and len(text) == 6:
            return _handle_link_code(db, msg)

        # Commands
        if text.startswith("/start"):
            return _handle_start(db, msg)
        if text.startswith("/help"):
            return _handle_help()
        if text.startswith("/link"):
            return _handle_link_instruction()
        if text.startswith("/businesses"):
            return _handle_businesses(db, msg)
        if text.startswith("/products"):
            return _handle_products(db, msg, text)
        if text.startswith("/sync"):
            return _handle_sync(db, msg, text)
        if text.startswith("/notifications"):
            return _handle_notifications(db, msg)
        if text.startswith("/status"):
            return _handle_status(db, msg)

        # If linked, treat unknown as help; if not linked, prompt linking
        user = _find_linked_user(db, msg.platform, msg.platform_user_id)
        if user is None:
            return BotReply(
                text=(
                    "حساب شما هنوز به سیستم متصل نیست.\n"
                    "برای اتصال:\n"
                    "1. وارد پنل وب شوید: /web/login\n"
                    "2. از بخش پروفایل، کد یک‌بارمصرف بسازید\n"
                    "3. کد ۶ رقمی را اینجا ارسال کنید\n\n"
                    "دستور /help برای راهنما"
                )
            )
        return _handle_help()

    finally:
        db.close()


def _handle_link_code(db: Session, msg: BotMessage) -> BotReply:
    try:
        user_id = verify_link_code(
            db,
            platform=enums.Platform(msg.platform),
            code=msg.text.strip(),
            platform_user_id=msg.platform_user_id,
        )
        db.commit()
        user = db.get(models.User, user_id)
        businesses = _list_user_businesses(db, user_id)
        btext = "\n".join(
            [f"- {b.business_name} ({b.business_type_key})" for b in businesses]
        ) or "هنوز کسب‌وکاری ندارید"
        return BotReply(
            text=(
                f"✅ حساب شما با موفقیت متصل شد!\n"
                f"کاربر: {user.display_name}\n\n"
                f"کسب‌وکارهای شما:\n{btext}\n\n"
                f"دستورات:\n"
                f"/businesses - لیست کسب‌وکارها\n"
                f"/products - لیست محصولات (آخرین کسب‌وکار)\n"
                f"/sync - همگام‌سازی\n"
                f"/notifications - اعلان‌ها\n"
                f"/help - راهنما"
            )
        )
    except Exception as exc:
        return BotReply(
            text=(
                f"❌ کد نامعتبر یا منقضی شده: {type(exc).__name__}\n"
                "لطفاً کد جدید بسازید و دوباره تلاش کنید."
            )
        )


def _handle_start(db: Session, msg: BotMessage) -> BotReply:
    user = _find_linked_user(db, msg.platform, msg.platform_user_id)
    if user:
        businesses = _list_user_businesses(db, user.user_id)
        btext = "\n".join([f"- {b.business_name}" for b in businesses]) or "هنوز کسب‌وکاری ندارید"
        return BotReply(
            text=(
                f"سلام {user.display_name}! 👋\n"
                f"حساب شما متصل است.\n\n"
                f"کسب‌وکارها:\n{btext}\n\n"
                f"/products - محصولات\n"
                f"/sync - همگام‌سازی\n"
                f"/notifications - اعلان‌ها\n"
                f"/help - راهنما"
            )
        )
    return BotReply(
        text=(
            "سلام! به دستیار هوشمند کسب‌وکارهای مجازی خوش آمدید 🤖\n\n"
            "برای استفاده از ربات:\n"
            "1. وارد پنل وب شوید\n"
            "2. کد اتصال یک‌بارمصرف بسازید\n"
            "3. کد ۶ رقمی را اینجا بفرستید\n\n"
            "دستورات:\n"
            "/link - آموزش اتصال\n"
            "/help - راهنما"
        )
    )


def _handle_help() -> BotReply:
    return BotReply(
        text=(
            "📚 راهنمای ربات دستیار هوشمند\n\n"
            "/start - شروع و وضعیت اتصال\n"
            "/link - آموزش اتصال حساب\n"
            "/businesses - لیست کسب‌وکارها\n"
            "/products [نام] - لیست محصولات (با فیلتر اختیاری)\n"
            "/sync [source_id] - اجرای همگام‌سازی\n"
            "/notifications - اعلان‌های اخیر\n"
            "/status - وضعیت سیستم\n"
            "/help - همین راهنما\n\n"
            "💡 نکته: برای اتصال، کد ۶ رقمی از پنل وب را اینجا ارسال کنید."
        )
    )


def _handle_link_instruction() -> BotReply:
    return BotReply(
        text=(
            "🔗 اتصال حساب\n\n"
            "1. وارد پنل وب شوید: /web/login\n"
            "2. به بخش پروفایل/اتصال‌ها بروید\n"
            "3. گزینه 'ساخت کد اتصال' را بزنید و پلتفرم Telegram را انتخاب کنید\n"
            "4. کد ۶ رقمی نمایش داده شده را اینجا ارسال کنید\n\n"
            "⏰ کد فقط ۱۰ دقیقه معتبر است و یک‌بار مصرف است.\n"
            "🔒 اتصال بر اساس تطبیق نام کاربری انجام نمی‌شود — فقط کد معتبر."
        )
    )


def _handle_businesses(db: Session, msg: BotMessage) -> BotReply:
    user = _find_linked_user(db, msg.platform, msg.platform_user_id)
    if not user:
        return BotReply(text="❌ ابتدا حساب خود را متصل کنید. کد ۶ رقمی را ارسال کنید.")
    businesses = _list_user_businesses(db, user.user_id)
    if not businesses:
        return BotReply(text="هنوز کسب‌وکاری ندارید. از پنل وب بسازید: /web/businesses")
    lines = ["🏢 کسب‌وکارهای شما:\n"]
    for b in businesses:
        lines.append(f"• {b.business_name} — {b.business_type_key} ({b.business_id})")
    lines.append("\nبرای دیدن محصولات: /products")
    return BotReply(text="\n".join(lines))


def _handle_products(db: Session, msg: BotMessage, text: str) -> BotReply:
    user = _find_linked_user(db, msg.platform, msg.platform_user_id)
    if not user:
        return BotReply(text="❌ ابتدا حساب خود را متصل کنید.")
    businesses = _list_user_businesses(db, user.user_id)
    if not businesses:
        return BotReply(text="کسب‌وکاری ندارید.")
    # Use first business for simplicity; future: store active business per user
    business = businesses[0]
    q = None
    parts = text.split(maxsplit=1)
    if len(parts) > 1:
        q = parts[1].strip()
    products = products_svc.list_products(db, business_id=business.business_id, q=q)
    if not products:
        return BotReply(text=f"محصولی یافت نشد (جستجو: {q or 'همه'})")
    lines = [f"📦 محصولات {business.business_name} (نمایش ۱۰ مورد اول):\n"]
    for p in products[:10]:
        lines.append(f"• {p.name} — {p.lifecycle_state} — {p.price or '—'}")
    lines.append(f"\nجمع کل: {len(products)} محصول")
    lines.append("برای همگام‌سازی: /sync")
    return BotReply(text="\n".join(lines))


def _handle_sync(db: Session, msg: BotMessage, text: str) -> BotReply:
    user = _find_linked_user(db, msg.platform, msg.platform_user_id)
    if not user:
        return BotReply(text="❌ ابتدا حساب خود را متصل کنید.")
    businesses = _list_user_businesses(db, user.user_id)
    if not businesses:
        return BotReply(text="کسب‌وکاری ندارید.")
    business = businesses[0]
    sources = sources_svc.list_sources(db, business_id=business.business_id)
    if not sources:
        return BotReply(text="منبعی ندارید. از پنل وب منبع بسازید.")
    # If source_id provided
    parts = text.split()
    target_source = None
    if len(parts) > 1:
        try:
            sid = uuid.UUID(parts[1])
            target_source = db.scalar(
                select(models.Source).where(
                    models.Source.source_id == sid,
                    models.Source.business_id == business.business_id,
                )
            )
        except Exception:
            pass
    if target_source is None:
        # Use first active source
        for s in sources:
            if s.status == enums.SourceStatus.ACTIVE.value:
                target_source = s
                break
        if target_source is None:
            target_source = sources[0]

    mapping = active_mapping_for(db, target_source)
    if mapping is None:
        return BotReply(text=f"منبع {target_source.name} نگاشت فعال ندارد.")

    # Check entitlement
    from app.application import entitlements as ent_svc

    try:
        ent_svc.require_entitlement_unchecked(db, business_id=business.business_id)
    except Exception as exc:
        return BotReply(text=f"❌ اشتراک فعال ندارید: {exc}")

    # Excel boundary
    if target_source.kind == enums.SourceKind.EXCEL_UPLOAD.value:
        return BotReply(
            text=(
                f"منبع {target_source.name} از نوع Excel است.\n"
                f"همگام‌سازی زمان‌بندی‌شده فقط برای Google Sheets پشتیبانی می‌شود.\n"
                f"لطفاً از پنل وب فایل جدید آپلود کنید."
            )
        )

    from app.domain.sync_policy import SyncPolicy

    try:
        job, created = sync_jobs_svc.enqueue(
            db,
            source=target_source,
            mapping=mapping,
            trigger=enums.SyncTrigger.MANUAL,
            requested_by=user.user_id,
            correlation_id=f"telegram-{msg.platform_user_id}-{uuid.uuid4().hex[:8]}",
            policy=SyncPolicy(),
        )
        db.commit()
        if not created:
            return BotReply(
                text=(
                    f"⏳ یک همگام‌سازی فعال برای {target_source.name} وجود دارد.\n"
                    f"وضعیت: {job.status}\n"
                    f"درخواست شما تجمیع شد."
                )
            )
        # Dispatch async
        try:
            from app.workers.sync_tasks import run_sync_job

            run_sync_job.delay(str(job.sync_id))
        except Exception:
            pass
        return BotReply(
            text=(
                f"✅ همگام‌سازی برای {target_source.name} آغاز شد.\n"
                f"شناسه: {job.sync_id}\n"
                f"وضعیت را با /notifications بررسی کنید."
            )
        )
    except ValueError as exc:
        return BotReply(text=f"❌ {exc}")
    except Exception as exc:
        return BotReply(text=f"❌ خطا در همگام‌سازی: {type(exc).__name__}")


def _handle_notifications(db: Session, msg: BotMessage) -> BotReply:
    user = _find_linked_user(db, msg.platform, msg.platform_user_id)
    if not user:
        return BotReply(text="❌ ابتدا حساب خود را متصل کنید.")
    businesses = _list_user_businesses(db, user.user_id)
    if not businesses:
        return BotReply(text="کسب‌وکاری ندارید.")
    business = businesses[0]
    notifs = db.scalars(
        select(models.Notification)
        .where(
            models.Notification.business_id == business.business_id,
            models.Notification.recipient_user_id == user.user_id,
        )
        .order_by(models.Notification.created_at.desc())
        .limit(5)
    ).all()
    if not notifs:
        return BotReply(text="اعلانی وجود ندارد.")
    lines = ["🔔 اعلان‌های اخیر:\n"]
    for n in notifs:
        status = "✓" if n.status == enums.NotificationStatus.READ.value else "•"
        lines.append(f"{status} {n.title}: {n.body[:100]}")
    return BotReply(text="\n".join(lines))


def _handle_status(db: Session, msg: BotMessage) -> BotReply:
    user = _find_linked_user(db, msg.platform, msg.platform_user_id)
    linked = "✅ متصل" if user else "❌ متصل نیست"
    return BotReply(
        text=(
            f"📊 وضعیت سیستم\n"
            f"حساب: {linked}\n"
            f"پلتفرم: {msg.platform}\n"
            f"کاربر پلتفرم: {msg.platform_user_id}\n\n"
            f"برای راهنما: /help"
        )
    )
