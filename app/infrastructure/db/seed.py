"""Reference-data seeding (single source of truth).

Used by the Alembic migrations AND by the test schema builder, so production
and tests never drift. The starter plan's price/limits are initial
placeholders: the owner tunes them through the plan-management endpoints
without code changes (Gate-approved plan-catalog behavior).
"""

from __future__ import annotations

import hashlib
import json
import uuid

import sqlalchemy as sa

#: Default business type (Phase 1).
BUSINESS_TYPE_SEED: dict[str, dict[str, object]] = {
    "general": {
        "key": "general",
        "name": "General business",
        "description": "Default business type.",
        "is_active": True,
    }
}

#: Starter plan (Phase 2). Prices in Toman; limits are initial values that
#: the operator can adjust at runtime.
STARTER_PLAN: dict[str, object] = {
    "code": "starter",
    "name": "Starter",
    "currency": "IRT",
    "price": 990_000,
    "billing_period": "monthly",
    "is_active": True,
    "product_limit": 100,
    "source_limit": 2,
    "channel_limit": 2,
    "sync_frequency_per_day": 3,
    "ai_available": True,
    "ai_monthly_credits": 50,
    # Per-product presets are exclusive to the TOP-tier plan (owner
    # decision 2026-09-13); the operator enables this flag on the top
    # plan at runtime. Starter is the entry-level plan -> off.
    "product_preset_eligible": False,
    "preset_customization": "basic",
    "report_level": "basic",
    "media_storage_limit_bytes": 1_073_741_824,  # 1 GiB
    "admin_seat_limit": 3,
    "feature_flags": {},
}


#: Phase 4: default AI output definitions (AI spec section 2 example keys).
#: Lightweight prompts per user requirement, template-consistent
AI_DEFINITION_SEED: list[dict[str, object]] = [
    {
        "key": "ai_description",
        "version": 1,
        "display_name": "توضیح کوتاه محصول",
        "prompt_template": (
            "توضیح کوتاه فارسی (1-2 جمله) برای: {name} | {category} | {attr.Brand} {attr.Model} | CPU:{attr.CPU} RAM:{attr.Ram} | {description}\n"
            "فقط توضیح، بدون قیمت/موجودی."
        ),
        "input_fields": ["name", "category", "description", "attr.Brand", "attr.Model", "attr.CPU", "attr.Ram"],
        "max_output_length": 300,
        "provider_policy": None,
        "cost_credits": 1,
        "retry_policy": "transient:3",
        "allowed_contexts": None,
        "active": True,
    },
    {
        "key": "ai_features",
        "version": 1,
        "display_name": "نقاط قوت",
        "prompt_template": (
            "3-5 نقطه قوت برای: {name} | {attr.Brand} {attr.Model} | {attr.CPU} {attr.Ram} {attr.Hard} | {description}\n"
            "فرمت: هر نقطه با 🔹 شروع، فارسی کوتاه."
        ),
        "input_fields": ["name", "description", "attr.Brand", "attr.Model", "attr.CPU", "attr.Ram", "attr.Hard", "attr.Weight", "attr.Battery life"],
        "max_output_length": 500,
        "provider_policy": None,
        "cost_credits": 1,
        "retry_policy": "transient:3",
        "allowed_contexts": None,
        "active": True,
    },
    {
        "key": "ai_games",
        "version": 1,
        "display_name": "بازی‌های قابل اجرا",
        "prompt_template": (
            "بازی‌های قابل اجرا با: {attr.CPU} | {attr.GPU} | {attr.Ram}\n"
            "فقط نام بازی‌ها با | جدا، مثل: Counter 1.6 | GTA V"
        ),
        "input_fields": ["attr.CPU", "attr.GPU", "attr.Ram"],
        "max_output_length": 200,
        "provider_policy": None,
        "cost_credits": 1,
        "retry_policy": "transient:3",
        "allowed_contexts": None,
        "active": True,
    },
    {
        "key": "ai_software",
        "version": 1,
        "display_name": "نرم‌افزارهای قابل اجرا",
        "prompt_template": (
            "نرم‌افزارهای قابل اجرا با: {attr.CPU} | {attr.Ram} | {name}\n"
            "فقط نام نرم‌افزارها با | جدا، مثل: فوتوشاپ | آفیس | وب گردی"
        ),
        "input_fields": ["attr.CPU", "attr.Ram", "name"],
        "max_output_length": 200,
        "provider_policy": None,
        "cost_credits": 1,
        "retry_policy": "transient:3",
        "allowed_contexts": None,
        "active": True,
    },
    {
        "key": "ai_short_title",
        "version": 1,
        "display_name": "عنوان کوتاه",
        "prompt_template": (
            "عنوان کوتاه (max 60 chars) برای: {name} | {attr.Brand} {attr.Model}\n"
            "فقط عنوان."
        ),
        "input_fields": ["name", "attr.Brand", "attr.Model"],
        "max_output_length": 100,
        "provider_policy": None,
        "cost_credits": 1,
        "retry_policy": "transient:3",
        "allowed_contexts": None,
        "active": True,
    },
    {
        "key": "ai_hashtags",
        "version": 1,
        "display_name": "هشتگ‌ها",
        "prompt_template": (
            "هشتگ فارسی/انگلیسی برای: {name} | {category} | {attr.Brand}\n"
            "فرمت: #Brand #Model #Category"
        ),
        "input_fields": ["name", "category", "attr.Brand", "attr.Model"],
        "max_output_length": 150,
        "provider_policy": None,
        "cost_credits": 1,
        "retry_policy": "transient:3",
        "allowed_contexts": None,
        "active": True,
    },
]

DEFAULT_PRESET_NAME = "Default preset"

#: Phase 4: default preset block list (platform-neutral, allowlisted tokens).
#: Template matching user's example EXACTLY — uses STATIC_TEXT with attr tokens for full control
#: Example output:
#: 💻 #Asus 1215N Mini
#: لپ تاپ زیبا...
#: ⭐️A
#: 🧠CPU: #Athom-D525
#: 🧩RAM: 2GB
#: 💾HARD: 250GB
#: 🎮GPU: Intel GMA 3150
#: 📐Screen: 12.1 اینچ
#: ✔️Weight: 1.46 کیلوگرم
#: 🔋Battery Life: تا 8 ساعت
#: ✨ نقاط قوت
#: 🔹 بسیار سبک...
#: 🎮⚙️ بازی ها و نرم افزار های قابل اجرا:
#: {ai_games}
#: {ai_software}
#: 💰 قیمت: {price} تومان
#: 📞 @Nick_Bri | 09102807430
DEFAULT_PRESET_BLOCKS: list[dict[str, object]] = [
    {"id": "title", "type": "STATIC_TEXT", "ownership": "SYSTEM_MANAGED",
     "payload": {"text": "💻 #{attr.Brand} {name}"}},
    {"id": "ai_desc", "type": "AI_OUTPUT", "ownership": "CUSTOMER_MANAGED",
     "payload": {"key": "ai_description"}},
    {"id": "grade", "type": "STATIC_TEXT", "ownership": "SYSTEM_MANAGED",
     "payload": {"text": "⭐️{attr.Grade}"}},
    {"id": "cpu", "type": "STATIC_TEXT", "ownership": "SYSTEM_MANAGED",
     "payload": {"text": "🧠CPU: {attr.CPU}"}},
    {"id": "ram", "type": "STATIC_TEXT", "ownership": "SYSTEM_MANAGED",
     "payload": {"text": "🧩RAM: {attr.Ram}"}},
    {"id": "hard", "type": "STATIC_TEXT", "ownership": "SYSTEM_MANAGED",
     "payload": {"text": "💾HARD: {attr.Hard}"}},
    {"id": "gpu", "type": "STATIC_TEXT", "ownership": "SYSTEM_MANAGED",
     "payload": {"text": "🎮GPU: {attr.GPU}"}},
    {"id": "resolution", "type": "STATIC_TEXT", "ownership": "SYSTEM_MANAGED",
     "payload": {"text": "📐Screen: {attr.Resolution}"}},
    {"id": "weight", "type": "STATIC_TEXT", "ownership": "SYSTEM_MANAGED",
     "payload": {"text": "✔️Weight: {attr.Weight}"}},
    {"id": "battery", "type": "STATIC_TEXT", "ownership": "SYSTEM_MANAGED",
     "payload": {"text": "🔋Battery Life: {attr.Battery life}"}},
    {"id": "sep_features", "type": "SEPARATOR", "ownership": "STATIC",
     "payload": {"text": "✨ نقاط قوت"}},
    {"id": "ai_features", "type": "AI_OUTPUT", "ownership": "CUSTOMER_MANAGED",
     "payload": {"key": "ai_features"}},
    {"id": "sep_games", "type": "SEPARATOR", "ownership": "STATIC",
     "payload": {"text": "🎮⚙️ بازی ها و نرم افزار های قابل اجرا:"}},
    {"id": "ai_games", "type": "AI_OUTPUT", "ownership": "CUSTOMER_MANAGED",
     "payload": {"key": "ai_games"}},
    {"id": "ai_software", "type": "AI_OUTPUT", "ownership": "CUSTOMER_MANAGED",
     "payload": {"key": "ai_software"}},
    {"id": "price", "type": "STATIC_TEXT", "ownership": "SYSTEM_MANAGED",
     "payload": {"text": "💰 قیمت: {price} تومان"}},
    {"id": "contact", "type": "STATIC_TEXT", "ownership": "CUSTOMER_MANAGED",
     "payload": {"text": "📞 @Nick_Bri | 09102807430"}},
    {"id": "warranty", "type": "STATIC_TEXT", "ownership": "STATIC",
     "payload": {"text": "🎁 یکماه گارانتی سخت افزاری مکتوب به همراه فاکتور معتبر | 💳 امکان خرید اقساطی با یک سوم پیش پرداخت"}},
    {"id": "tags", "type": "HASHTAG_SET", "ownership": "CUSTOMER_MANAGED",
     "payload": {}},
]


def seed_reference_data(conn: sa.Connection) -> None:
    """Insert reference data if absent (idempotent)."""
    from app.domain.content import validate_blocks
    from app.infrastructure.db.models import (
        AIOutputDefinition,
        BusinessType,
        Plan,
        Preset,
        PresetVersion,
    )

    existing_types = conn.execute(sa.select(BusinessType.key)).all()
    existing_keys = {row[0] for row in existing_types}
    for row in BUSINESS_TYPE_SEED.values():
        if row["key"] not in existing_keys:
            conn.execute(BusinessType.__table__.insert().values([row]))

    if conn.execute(sa.select(Plan.code).where(Plan.code == STARTER_PLAN["code"])).first() is None:
        conn.execute(Plan.__table__.insert().values([dict(STARTER_PLAN)]))

    # --- Phase 4: AI output definitions (idempotent per key+version) ---
    for spec in AI_DEFINITION_SEED:
        exists = conn.execute(
            sa.select(AIOutputDefinition.definition_id).where(
                AIOutputDefinition.key == spec["key"],
                AIOutputDefinition.version == spec["version"],
            )
        ).first()
        if exists is None:
            conn.execute(
                AIOutputDefinition.__table__.insert().values(
                    [dict(spec, definition_id=uuid.uuid4(), created_by=None)]
                )
            )

    # --- Phase 4: default preset per active business type ---
    preset_blocks = DEFAULT_PRESET_BLOCKS
    blocks_payload = [
        {
            "id": b["id"],
            "type": b["type"],
            "ownership": b["ownership"],
            "payload": b["payload"],
        }
        for b in preset_blocks
    ]
    # Validate against the seeded AI keys (guards the seed itself).
    validate_blocks(blocks_payload, frozenset(s["key"] for s in AI_DEFINITION_SEED))
    content_hash = hashlib.sha256(
        json.dumps(blocks_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    all_type_keys = [row[0] for row in conn.execute(sa.select(BusinessType.key)).all()]
    for btype_key in all_type_keys:
        has_preset = conn.execute(
            sa.select(Preset.preset_id).where(
                Preset.business_type_key == btype_key,
                Preset.name == DEFAULT_PRESET_NAME,
            )
        ).first()
        if has_preset is None:
            preset_id = uuid.uuid4()
            conn.execute(
                Preset.__table__.insert().values(
                    [
                        {
                            "preset_id": preset_id,
                            "business_type_key": btype_key,
                            "name": DEFAULT_PRESET_NAME,
                            "description": "Default preset for this business type.",
                            "is_default": True,
                            "created_by": None,
                        }
                    ]
                )
            )
            conn.execute(
                PresetVersion.__table__.insert().values(
                    [
                        {
                            "version_id": uuid.uuid4(),
                            "preset_id": preset_id,
                            "version": 1,
                            "blocks": blocks_payload,
                            "content_hash": content_hash,
                            "status": "ACTIVE",
                            "created_by": None,
                        }
                    ]
                )
            )

    conn.commit()
