"""Computer business type + gold plan + flexible template (value_map).

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-09-18

- Adds business_type 'computer' for laptop/computer shops and ensures 'general' exists
- Adds plans 'gold' and 'pro' with product_preset_eligible=True and custom_value_mapping flag
- Updates default presets per business_type: general simple, computer detailed with Touch value_map
- Ensures preset validation for new flexible CUSTOM_FIELD payload (value_map, hide_on)

Idempotent.
"""

from __future__ import annotations

from collections.abc import Sequence
import hashlib
import json
import uuid

import sqlalchemy as sa
from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: str | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BUSINESS_TYPES = {
    "general": {
        "key": "general",
        "name": "General",
        "description": "کسب‌وکار عمومی — برای فروشگاه‌های ساده با محصولات متنوع. قالب ساده با نام، توضیحات، قیمت، موجودی.",
        "is_active": True,
    },
    "computer": {
        "key": "computer",
        "name": "Computer / Laptop",
        "description": "فروشگاه کامپیوتر و لپ‌تاپ — قالب تخصصی با CPU، RAM، گرافیک، تاچ، وزن، باتری، نقاط قوت، بازی‌ها و نرم‌افزارهای قابل اجرا. پشتیبانی از نگاشت مقادیر سفارشی (yes→موجود).",
        "is_active": True,
    },
}

PLANS = [
    {
        "code": "gold",
        "name": "Gold",
        "currency": "IRT",
        "price": 1_990_000,
        "billing_period": "monthly",
        "is_active": True,
        "product_limit": 500,
        "source_limit": 5,
        "channel_limit": 5,
        "sync_frequency_per_day": 10,
        "ai_available": True,
        "ai_monthly_credits": 200,
        "product_preset_eligible": True,
        "preset_customization": "advanced",
        "report_level": "advanced",
        "media_storage_limit_bytes": 5_368_709_120,
        "admin_seat_limit": 10,
        "feature_flags": {"custom_value_mapping": True, "flexible_template": True},
    },
    {
        "code": "pro",
        "name": "Pro",
        "currency": "IRT",
        "price": 3_990_000,
        "billing_period": "monthly",
        "is_active": True,
        "product_limit": 2000,
        "source_limit": 20,
        "channel_limit": 20,
        "sync_frequency_per_day": 24,
        "ai_available": True,
        "ai_monthly_credits": 1000,
        "product_preset_eligible": True,
        "preset_customization": "advanced",
        "report_level": "advanced",
        "media_storage_limit_bytes": 20_971_520_000,
        "admin_seat_limit": 50,
        "feature_flags": {"custom_value_mapping": True, "flexible_template": True, "all_features": True},
    },
]

GENERAL_BLOCKS = [
    {"id": "title", "type": "STATIC_TEXT", "ownership": "SYSTEM_MANAGED", "payload": {"text": "📦 {name}"}},
    {"id": "desc", "type": "STATIC_TEXT", "ownership": "SYSTEM_MANAGED", "payload": {"text": "{description}"}},
    {"id": "ai_desc", "type": "AI_OUTPUT", "ownership": "CUSTOMER_MANAGED", "payload": {"key": "ai_description"}},
    {"id": "price", "type": "STATIC_TEXT", "ownership": "SYSTEM_MANAGED", "payload": {"text": "💰 قیمت: {price} تومان"}},
    {"id": "stock", "type": "STATIC_TEXT", "ownership": "SYSTEM_MANAGED", "payload": {"text": "📦 موجودی: {stock}"}},
    {"id": "contact", "type": "STATIC_TEXT", "ownership": "CUSTOMER_MANAGED", "payload": {"text": "📞 @Nick_Bri | 09102807430"}},
    {"id": "tags", "type": "HASHTAG_SET", "ownership": "CUSTOMER_MANAGED", "payload": {}},
]

COMPUTER_BLOCKS = [
    {"id": "title", "type": "STATIC_TEXT", "ownership": "SYSTEM_MANAGED", "payload": {"text": "💻 #{attr.Brand} {name}"}},
    {"id": "ai_desc", "type": "AI_OUTPUT", "ownership": "CUSTOMER_MANAGED", "payload": {"key": "ai_description"}},
    {"id": "grade", "type": "CUSTOM_FIELD", "ownership": "SYSTEM_MANAGED", "payload": {"key": "Grade", "display_name": "⭐️", "separator": "", "hide_if_empty": True}},
    {"id": "cpu", "type": "CUSTOM_FIELD", "ownership": "SYSTEM_MANAGED", "payload": {"key": "CPU", "display_name": "🧠CPU", "hide_if_empty": True}},
    {"id": "ram", "type": "CUSTOM_FIELD", "ownership": "SYSTEM_MANAGED", "payload": {"key": "Ram", "display_name": "🧩RAM", "hide_if_empty": True}},
    {"id": "hard", "type": "CUSTOM_FIELD", "ownership": "SYSTEM_MANAGED", "payload": {"key": "Hard", "display_name": "💾HARD", "hide_if_empty": True}},
    {"id": "gpu", "type": "CUSTOM_FIELD", "ownership": "SYSTEM_MANAGED", "payload": {"key": "GPU", "display_name": "🎮GPU", "hide_if_empty": True}},
    {"id": "resolution", "type": "CUSTOM_FIELD", "ownership": "SYSTEM_MANAGED", "payload": {"key": "Resolution", "display_name": "📐Screen", "hide_if_empty": True}},
    {"id": "weight", "type": "CUSTOM_FIELD", "ownership": "SYSTEM_MANAGED", "payload": {"key": "Weight", "display_name": "✔️Weight", "hide_if_empty": True}},
    {"id": "battery", "type": "CUSTOM_FIELD", "ownership": "SYSTEM_MANAGED", "payload": {"key": "Battery life", "display_name": "🔋Battery Life", "hide_if_empty": True}},
    {"id": "touch", "type": "CUSTOM_FIELD", "ownership": "CUSTOMER_MANAGED", "payload": {"key": "Touch", "display_name": "👆 تاچ", "value_map": {"yes": "دارای تاچ اسکرین", "Yes": "دارای تاچ اسکرین", "no": "", "No": ""}, "hide_on": ["no", "false", "0", ""], "hide_if_empty": True, "separator": ": "}},
    {"id": "pen", "type": "CUSTOM_FIELD", "ownership": "CUSTOMER_MANAGED", "payload": {"key": "Pen", "display_name": "🖊️ قلم", "value_map": {"yes": "پشتیبانی از قلم", "no": ""}, "hide_on": ["no", "false", "0", ""], "hide_if_empty": True}},
    {"id": "x360", "type": "CUSTOM_FIELD", "ownership": "CUSTOMER_MANAGED", "payload": {"key": "X360", "display_name": "🔄 چرخش", "value_map": {"yes": "360 درجه", "no": ""}, "hide_on": ["no", "false", "0", ""], "hide_if_empty": True}},
    {"id": "sep_features", "type": "SEPARATOR", "ownership": "STATIC", "payload": {"text": "✨ نقاط قوت"}},
    {"id": "ai_features", "type": "AI_OUTPUT", "ownership": "CUSTOMER_MANAGED", "payload": {"key": "ai_features"}},
    {"id": "sep_games", "type": "SEPARATOR", "ownership": "STATIC", "payload": {"text": "🎮⚙️ بازی ها و نرم افزار های قابل اجرا:"}},
    {"id": "ai_games", "type": "AI_OUTPUT", "ownership": "CUSTOMER_MANAGED", "payload": {"key": "ai_games"}},
    {"id": "ai_software", "type": "AI_OUTPUT", "ownership": "CUSTOMER_MANAGED", "payload": {"key": "ai_software"}},
    {"id": "price", "type": "STATIC_TEXT", "ownership": "SYSTEM_MANAGED", "payload": {"text": "💰 قیمت: {price} تومان"}},
    {"id": "contact", "type": "STATIC_TEXT", "ownership": "CUSTOMER_MANAGED", "payload": {"text": "📞 @Nick_Bri | 09102807430"}},
    {"id": "warranty", "type": "STATIC_TEXT", "ownership": "STATIC", "payload": {"text": "🎁 یکماه گارانتی سخت افزاری مکتوب به همراه فاکتور معتبر | 💳 امکان خرید اقساطی با یک سوم پیش پرداخت"}},
    {"id": "tags", "type": "HASHTAG_SET", "ownership": "CUSTOMER_MANAGED", "payload": {}},
]

PRESET_BY_TYPE = {
    "general": GENERAL_BLOCKS,
    "computer": COMPUTER_BLOCKS,
}

DEFAULT_PRESET_NAME = "Default preset"


def upgrade() -> None:
    conn = op.get_bind()

    # Business types
    btypes_tbl = sa.table(
        "business_types",
        sa.column("key", sa.String),
        sa.column("name", sa.String),
        sa.column("description", sa.String),
        sa.column("is_active", sa.Boolean),
    )
    for key, data in BUSINESS_TYPES.items():
        exists = conn.execute(sa.select(btypes_tbl.c.key).where(btypes_tbl.c.key == key)).first()
        if not exists:
            conn.execute(btypes_tbl.insert().values(**data))

    # Plans - no description column in actual model
    plans_tbl = sa.table(
        "plans",
        sa.column("plan_id", sa.Uuid),
        sa.column("code", sa.String),
        sa.column("name", sa.String),
        sa.column("currency", sa.String),
        sa.column("price", sa.Integer),
        sa.column("billing_period", sa.String),
        sa.column("is_active", sa.Boolean),
        sa.column("product_limit", sa.Integer),
        sa.column("source_limit", sa.Integer),
        sa.column("channel_limit", sa.Integer),
        sa.column("sync_frequency_per_day", sa.Integer),
        sa.column("ai_available", sa.Boolean),
        sa.column("ai_monthly_credits", sa.Integer),
        sa.column("product_preset_eligible", sa.Boolean),
        sa.column("preset_customization", sa.String),
        sa.column("report_level", sa.String),
        sa.column("media_storage_limit_bytes", sa.Integer),
        sa.column("admin_seat_limit", sa.Integer),
        sa.column("feature_flags", sa.JSON),
    )
    for plan in PLANS:
        exists = conn.execute(sa.select(plans_tbl.c.code).where(plans_tbl.c.code == plan["code"])).first()
        if not exists:
            conn.execute(plans_tbl.insert().values(plan_id=uuid.uuid4(), **plan))
        else:
            # Update to ensure flags
            conn.execute(
                plans_tbl.update()
                .where(plans_tbl.c.code == plan["code"])
                .values(
                    product_preset_eligible=plan["product_preset_eligible"],
                    preset_customization=plan["preset_customization"],
                    feature_flags=plan["feature_flags"],
                    is_active=True,
                )
            )

    # Presets per business type
    presets_tbl = sa.table(
        "presets",
        sa.column("preset_id", sa.Uuid),
        sa.column("business_type_key", sa.String),
        sa.column("name", sa.String),
        sa.column("description", sa.String),
        sa.column("is_default", sa.Boolean),
        sa.column("created_by", sa.Uuid),
    )
    preset_versions_tbl = sa.table(
        "preset_versions",
        sa.column("version_id", sa.Uuid),
        sa.column("preset_id", sa.Uuid),
        sa.column("version", sa.Integer),
        sa.column("blocks", sa.JSON),
        sa.column("content_hash", sa.String),
        sa.column("status", sa.String),
        sa.column("created_by", sa.Uuid),
    )

    for btype_key, blocks in PRESET_BY_TYPE.items():
        content_hash = hashlib.sha256(json.dumps(blocks, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        preset_row = conn.execute(
            sa.select(presets_tbl.c.preset_id).where(
                presets_tbl.c.business_type_key == btype_key,
                presets_tbl.c.name == DEFAULT_PRESET_NAME,
            )
        ).first()
        if preset_row is None:
            preset_id = uuid.uuid4()
            conn.execute(
                presets_tbl.insert().values(
                    preset_id=preset_id,
                    business_type_key=btype_key,
                    name=DEFAULT_PRESET_NAME,
                    description=f"Default preset for {btype_key}",
                    is_default=True,
                    created_by=None,
                )
            )
            conn.execute(
                preset_versions_tbl.insert().values(
                    version_id=uuid.uuid4(),
                    preset_id=preset_id,
                    version=1,
                    blocks=blocks,
                    content_hash=content_hash,
                    status="ACTIVE",
                    created_by=None,
                )
            )
        else:
            preset_id = preset_row[0]
            active = conn.execute(
                sa.select(preset_versions_tbl.c.version_id, preset_versions_tbl.c.content_hash, preset_versions_tbl.c.version)
                .where(
                    preset_versions_tbl.c.preset_id == preset_id,
                    preset_versions_tbl.c.status == "ACTIVE",
                )
                .order_by(preset_versions_tbl.c.version.desc())
                .limit(1)
            ).first()
            if active and active[1] == content_hash:
                continue
            if active:
                conn.execute(
                    preset_versions_tbl.update()
                    .where(preset_versions_tbl.c.version_id == active[0])
                    .values(status="SUPERSEDED")
                )
                next_ver = (active[2] or 0) + 1
            else:
                max_ver = conn.execute(
                    sa.select(sa.func.max(preset_versions_tbl.c.version)).where(
                        preset_versions_tbl.c.preset_id == preset_id
                    )
                ).scalar() or 0
                next_ver = max_ver + 1
            conn.execute(
                preset_versions_tbl.insert().values(
                    version_id=uuid.uuid4(),
                    preset_id=preset_id,
                    version=next_ver,
                    blocks=blocks,
                    content_hash=content_hash,
                    status="ACTIVE",
                    created_by=None,
                )
            )


def downgrade() -> None:
    conn = op.get_bind()
    # Remove computer business type if no businesses use it
    btypes_tbl = sa.table("business_types", sa.column("key", sa.String))
    businesses_tbl = sa.table("businesses", sa.column("business_type_key", sa.String))
    used = conn.execute(sa.select(businesses_tbl.c.business_type_key).where(businesses_tbl.c.business_type_key == "computer").limit(1)).first()
    if not used:
        conn.execute(sa.delete(btypes_tbl).where(btypes_tbl.c.key == "computer"))
    # Remove gold/pro plans if not in use
    plans_tbl = sa.table("plans", sa.column("code", sa.String))
    subs_tbl = sa.table("subscriptions", sa.column("plan_id", sa.Uuid), sa.column("pending_plan_id", sa.Uuid))
    # For simplicity, keep plans on downgrade (non-destructive)
