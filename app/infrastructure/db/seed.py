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

#: Business types — general for simple shops, computer for laptop/computer shops with detailed specs
BUSINESS_TYPE_SEED: dict[str, dict[str, object]] = {
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

#: Plans (Phase 2). Prices in Toman; limits are initial values that
#: the operator can adjust at runtime. Added gold plan for custom mapping feature.
PLANS_SEED: list[dict[str, object]] = [
    {
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
        "product_preset_eligible": False,
        "preset_customization": "basic",
        "report_level": "basic",
        "media_storage_limit_bytes": 1_073_741_824,
        "admin_seat_limit": 3,
        "feature_flags": {},
    },
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
        # Gold plan CAN use custom mapping (option 2) — per user requirement
        "product_preset_eligible": True,
        "preset_customization": "advanced",
        "report_level": "advanced",
        "media_storage_limit_bytes": 2147483647,  # max int32, ~2GiB (5GiB overflows Integer)
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
        "media_storage_limit_bytes": 2147483647,  # max int32
        "admin_seat_limit": 50,
        "feature_flags": {"custom_value_mapping": True, "flexible_template": True, "all_features": True},
    },
]

# Keep STARTER_PLAN for backwards compat
STARTER_PLAN = PLANS_SEED[0]


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

# --- Presets per business type (flexible, per user requirement) ---
# general: simple template for private/simple shops
GENERAL_PRESET_BLOCKS: list[dict[str, object]] = [
    {"id": "title", "type": "STATIC_TEXT", "ownership": "SYSTEM_MANAGED",
     "payload": {"text": "📦 {name}"}},
    {"id": "desc", "type": "STATIC_TEXT", "ownership": "SYSTEM_MANAGED",
     "payload": {"text": "{description}"}},
    {"id": "ai_desc", "type": "AI_OUTPUT", "ownership": "CUSTOMER_MANAGED",
     "payload": {"key": "ai_description"}},
    {"id": "price", "type": "STATIC_TEXT", "ownership": "SYSTEM_MANAGED",
     "payload": {"text": "💰 قیمت: {price} تومان"}},
    {"id": "stock", "type": "STATIC_TEXT", "ownership": "SYSTEM_MANAGED",
     "payload": {"text": "📦 موجودی: {stock}"}},
    {"id": "contact", "type": "STATIC_TEXT", "ownership": "CUSTOMER_MANAGED",
     "payload": {"text": "📞 @Nick_Bri | 09102807430"}},
    {"id": "tags", "type": "HASHTAG_SET", "ownership": "CUSTOMER_MANAGED",
     "payload": {}},
]

# computer: detailed template matching user's example, with flexible Touch etc.
# Demonstrates value_map feature: Touch yes→موجود/دارای تاچ, no→hide or custom
COMPUTER_PRESET_BLOCKS: list[dict[str, object]] = [
    {"id": "title", "type": "STATIC_TEXT", "ownership": "SYSTEM_MANAGED",
     "payload": {"text": "💻 #{attr.Brand} {name}"}},
    {"id": "ai_desc", "type": "AI_OUTPUT", "ownership": "CUSTOMER_MANAGED",
     "payload": {"key": "ai_description"}},
    {"id": "grade", "type": "CUSTOM_FIELD", "ownership": "SYSTEM_MANAGED",
     "payload": {"key": "Grade", "display_name": "⭐️", "separator": "", "hide_if_empty": True}},
    {"id": "cpu", "type": "CUSTOM_FIELD", "ownership": "SYSTEM_MANAGED",
     "payload": {"key": "CPU", "display_name": "🧠CPU", "hide_if_empty": True}},
    {"id": "ram", "type": "CUSTOM_FIELD", "ownership": "SYSTEM_MANAGED",
     "payload": {"key": "Ram", "display_name": "🧩RAM", "hide_if_empty": True}},
    {"id": "hard", "type": "CUSTOM_FIELD", "ownership": "SYSTEM_MANAGED",
     "payload": {"key": "Hard", "display_name": "💾HARD", "hide_if_empty": True}},
    {"id": "gpu", "type": "CUSTOM_FIELD", "ownership": "SYSTEM_MANAGED",
     "payload": {"key": "GPU", "display_name": "🎮GPU", "hide_if_empty": True}},
    {"id": "resolution", "type": "CUSTOM_FIELD", "ownership": "SYSTEM_MANAGED",
     "payload": {"key": "Resolution", "display_name": "📐Screen", "hide_if_empty": True}},
    {"id": "weight", "type": "CUSTOM_FIELD", "ownership": "SYSTEM_MANAGED",
     "payload": {"key": "Weight", "display_name": "✔️Weight", "hide_if_empty": True}},
    {"id": "battery", "type": "CUSTOM_FIELD", "ownership": "SYSTEM_MANAGED",
     "payload": {"key": "Battery life", "display_name": "🔋Battery Life", "hide_if_empty": True}},
    # Example of flexible boolean field: Touch with custom mapping (golden plan feature)
    # If sheet has yes → show "دارای تاچ اسکرین", if no → hide or show custom
    {"id": "touch", "type": "CUSTOM_FIELD", "ownership": "CUSTOMER_MANAGED",
     "payload": {
         "key": "Touch",
         "display_name": "👆 تاچ",
         "value_map": {"yes": "دارای تاچ اسکرین", "Yes": "دارای تاچ اسکرین", "no": "", "No": ""},
         "hide_on": ["no", "false", "0", ""],
         "hide_if_empty": True,
         "separator": ": "
     }},
    {"id": "pen", "type": "CUSTOM_FIELD", "ownership": "CUSTOMER_MANAGED",
     "payload": {
         "key": "Pen",
         "display_name": "🖊️ قلم",
         "value_map": {"yes": "پشتیبانی از قلم", "no": ""},
         "hide_on": ["no", "false", "0", ""],
         "hide_if_empty": True
     }},
    {"id": "x360", "type": "CUSTOM_FIELD", "ownership": "CUSTOMER_MANAGED",
     "payload": {
         "key": "X360",
         "display_name": "🔄 چرخش",
         "value_map": {"yes": "360 درجه", "no": ""},
         "hide_on": ["no", "false", "0", ""],
         "hide_if_empty": True
     }},
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

# For backwards compatibility, DEFAULT_PRESET_BLOCKS = computer preset (most detailed)
DEFAULT_PRESET_BLOCKS = COMPUTER_PRESET_BLOCKS

# Mapping business_type -> preset blocks
PRESET_BLOCKS_BY_TYPE: dict[str, list[dict[str, object]]] = {
    "general": GENERAL_PRESET_BLOCKS,
    "computer": COMPUTER_PRESET_BLOCKS,
}


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

    # Seed all plans (starter, gold, pro) - filter to actual table columns (no description)
    for plan_data in PLANS_SEED:
        # Only keep columns that exist in Plan model
        clean = {k: v for k, v in plan_data.items() if k != "description"}
        if conn.execute(sa.select(Plan.code).where(Plan.code == clean["code"])).first() is None:
            conn.execute(Plan.__table__.insert().values([clean]))
        else:
            # Update existing plan to ensure gold features are present (idempotent)
            if clean["code"] in ("gold", "pro"):
                conn.execute(
                    Plan.__table__.update()
                    .where(Plan.code == clean["code"])
                    .values(
                        product_preset_eligible=clean["product_preset_eligible"],
                        preset_customization=clean["preset_customization"],
                        feature_flags=clean["feature_flags"],
                        is_active=True,
                    )
                )

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

    # --- Phase 4: default preset per business type (flexible, per type) ---
    ai_keys = frozenset(s["key"] for s in AI_DEFINITION_SEED)
    all_type_keys = [row[0] for row in conn.execute(sa.select(BusinessType.key)).all()]
    for btype_key in all_type_keys:
        # Pick blocks for this type, fallback to general or default
        preset_blocks = PRESET_BLOCKS_BY_TYPE.get(btype_key) or PRESET_BLOCKS_BY_TYPE.get("general") or DEFAULT_PRESET_BLOCKS
        blocks_payload = [
            {
                "id": b["id"],
                "type": b["type"],
                "ownership": b["ownership"],
                "payload": b["payload"],
            }
            for b in preset_blocks
        ]
        # Validate against the seeded AI keys
        try:
            validate_blocks(blocks_payload, ai_keys)
        except Exception as e:
            # If validation fails for this type, skip (should not happen)
            print(f"Preset validation failed for {btype_key}: {e}")
            continue
        content_hash = hashlib.sha256(
            json.dumps(blocks_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()

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
                            "description": f"Default preset for {btype_key} business type.",
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
        else:
            # If preset exists but content_hash differs, create new version (idempotent update)
            existing_version = conn.execute(
                sa.select(PresetVersion.version, PresetVersion.content_hash, PresetVersion.version_id)
                .where(PresetVersion.preset_id == has_preset[0])
                .order_by(PresetVersion.version.desc())
                .limit(1)
            ).first()
            if existing_version and existing_version[1] != content_hash:
                # Supersede old active
                conn.execute(
                    PresetVersion.__table__.update()
                    .where(PresetVersion.version_id == existing_version[2])
                    .values(status="SUPERSEDED")
                )
                conn.execute(
                    PresetVersion.__table__.insert().values(
                        [
                            {
                                "version_id": uuid.uuid4(),
                                "preset_id": has_preset[0],
                                "version": existing_version[0] + 1,
                                "blocks": blocks_payload,
                                "content_hash": content_hash,
                                "status": "ACTIVE",
                                "created_by": None,
                            }
                        ]
                    )
                )

    conn.commit()
