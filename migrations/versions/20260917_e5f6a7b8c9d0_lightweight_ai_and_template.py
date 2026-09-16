"""Lightweight AI prompts + template-consistent preset (user requirement).

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-09-17

- Updates ai_description prompt to lightweight (minimal tokens) per user: "توضیح کوتاه فارسی..."
- Inserts new AI definitions: ai_features, ai_games, ai_software, ai_short_title, ai_hashtags
- Updates default preset blocks to match user's exact example template:
  💻 {Brand} {name}, {ai_description}, Grade, CPU, RAM, HARD, GPU, Screen, Weight, Battery Life,
  ✨ نقاط قوت {ai_features}, 🎮⚙️ {ai_games} {ai_software}, price, contact, warranty, hashtags

Fixes: business_detail UUID truncate bug is template-only (no migration needed), but we also ensure preset validation passes with new TOKEN_RE allowing spaces in attr keys.

Idempotent: safe to run multiple times.
"""

from __future__ import annotations

from collections.abc import Sequence
import hashlib
import json
import uuid

import sqlalchemy as sa
from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: str | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Lightweight definitions matching seed.py
AI_DEFS = [
    {
        "key": "ai_description",
        "version": 1,
        "display_name": "توضیح کوتاه محصول",
        "prompt_template": "توضیح کوتاه فارسی (1-2 جمله) برای: {name} | {category} | {attr.Brand} {attr.Model} | CPU:{attr.CPU} RAM:{attr.Ram} | {description}\nفقط توضیح، بدون قیمت/موجودی.",
        "input_fields": ["name", "category", "description", "attr.Brand", "attr.Model", "attr.CPU", "attr.Ram"],
        "max_output_length": 300,
        "cost_credits": 1,
    },
    {
        "key": "ai_features",
        "version": 1,
        "display_name": "نقاط قوت",
        "prompt_template": "3-5 نقطه قوت برای: {name} | {attr.Brand} {attr.Model} | {attr.CPU} {attr.Ram} {attr.Hard} | {description}\nفرمت: هر نقطه با 🔹 شروع، فارسی کوتاه.",
        "input_fields": ["name", "description", "attr.Brand", "attr.Model", "attr.CPU", "attr.Ram", "attr.Hard", "attr.Weight", "attr.Battery life"],
        "max_output_length": 500,
        "cost_credits": 1,
    },
    {
        "key": "ai_games",
        "version": 1,
        "display_name": "بازی‌های قابل اجرا",
        "prompt_template": "بازی‌های قابل اجرا با: {attr.CPU} | {attr.GPU} | {attr.Ram}\nفقط نام بازی‌ها با | جدا، مثل: Counter 1.6 | GTA V",
        "input_fields": ["attr.CPU", "attr.GPU", "attr.Ram"],
        "max_output_length": 200,
        "cost_credits": 1,
    },
    {
        "key": "ai_software",
        "version": 1,
        "display_name": "نرم‌افزارهای قابل اجرا",
        "prompt_template": "نرم‌افزارهای قابل اجرا با: {attr.CPU} | {attr.Ram} | {name}\nفقط نام نرم‌افزارها با | جدا، مثل: فوتوشاپ | آفیس | وب گردی",
        "input_fields": ["attr.CPU", "attr.Ram", "name"],
        "max_output_length": 200,
        "cost_credits": 1,
    },
    {
        "key": "ai_short_title",
        "version": 1,
        "display_name": "عنوان کوتاه",
        "prompt_template": "عنوان کوتاه (max 60 chars) برای: {name} | {attr.Brand} {attr.Model}\nفقط عنوان.",
        "input_fields": ["name", "attr.Brand", "attr.Model"],
        "max_output_length": 100,
        "cost_credits": 1,
    },
    {
        "key": "ai_hashtags",
        "version": 1,
        "display_name": "هشتگ‌ها",
        "prompt_template": "هشتگ فارسی/انگلیسی برای: {name} | {category} | {attr.Brand}\nفرمت: #Brand #Model #Category",
        "input_fields": ["name", "category", "attr.Brand", "attr.Model"],
        "max_output_length": 150,
        "cost_credits": 1,
    },
]

DEFAULT_PRESET_NAME = "Default preset"

DEFAULT_PRESET_BLOCKS = [
    {"id": "title", "type": "STATIC_TEXT", "ownership": "SYSTEM_MANAGED", "payload": {"text": "💻 #{attr.Brand} {name}"}},
    {"id": "ai_desc", "type": "AI_OUTPUT", "ownership": "CUSTOMER_MANAGED", "payload": {"key": "ai_description"}},
    {"id": "grade", "type": "STATIC_TEXT", "ownership": "SYSTEM_MANAGED", "payload": {"text": "⭐️{attr.Grade}"}},
    {"id": "cpu", "type": "STATIC_TEXT", "ownership": "SYSTEM_MANAGED", "payload": {"text": "🧠CPU: {attr.CPU}"}},
    {"id": "ram", "type": "STATIC_TEXT", "ownership": "SYSTEM_MANAGED", "payload": {"text": "🧩RAM: {attr.Ram}"}},
    {"id": "hard", "type": "STATIC_TEXT", "ownership": "SYSTEM_MANAGED", "payload": {"text": "💾HARD: {attr.Hard}"}},
    {"id": "gpu", "type": "STATIC_TEXT", "ownership": "SYSTEM_MANAGED", "payload": {"text": "🎮GPU: {attr.GPU}"}},
    {"id": "resolution", "type": "STATIC_TEXT", "ownership": "SYSTEM_MANAGED", "payload": {"text": "📐Screen: {attr.Resolution}"}},
    {"id": "weight", "type": "STATIC_TEXT", "ownership": "SYSTEM_MANAGED", "payload": {"text": "✔️Weight: {attr.Weight}"}},
    {"id": "battery", "type": "STATIC_TEXT", "ownership": "SYSTEM_MANAGED", "payload": {"text": "🔋Battery Life: {attr.Battery life}"}},
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


def upgrade() -> None:
    conn = op.get_bind()

    # --- AI definitions: upsert ---
    ai_defs_tbl = sa.table(
        "ai_output_definitions",
        sa.column("definition_id", sa.Uuid),
        sa.column("key", sa.String),
        sa.column("version", sa.Integer),
        sa.column("display_name", sa.String),
        sa.column("prompt_template", sa.Text),
        sa.column("input_fields", sa.JSON),
        sa.column("max_output_length", sa.Integer),
        sa.column("provider_policy", sa.String),
        sa.column("cost_credits", sa.Integer),
        sa.column("retry_policy", sa.String),
        sa.column("allowed_contexts", sa.JSON),
        sa.column("active", sa.Boolean),
        sa.column("created_by", sa.Uuid),
    )

    for spec in AI_DEFS:
        existing = conn.execute(
            sa.select(ai_defs_tbl.c.definition_id).where(
                ai_defs_tbl.c.key == spec["key"],
                ai_defs_tbl.c.version == spec["version"],
            )
        ).first()
        if existing is None:
            conn.execute(
                ai_defs_tbl.insert().values(
                    definition_id=uuid.uuid4(),
                    key=spec["key"],
                    version=spec["version"],
                    display_name=spec["display_name"],
                    prompt_template=spec["prompt_template"],
                    input_fields=spec["input_fields"],
                    max_output_length=spec["max_output_length"],
                    provider_policy=None,
                    cost_credits=spec["cost_credits"],
                    retry_policy="transient:3",
                    allowed_contexts=None,
                    active=True,
                    created_by=None,
                )
            )
        else:
            # Update existing prompt to lightweight version (for ai_description)
            conn.execute(
                ai_defs_tbl.update()
                .where(
                    ai_defs_tbl.c.key == spec["key"],
                    ai_defs_tbl.c.version == spec["version"],
                )
                .values(
                    display_name=spec["display_name"],
                    prompt_template=spec["prompt_template"],
                    input_fields=spec["input_fields"],
                    max_output_length=spec["max_output_length"],
                    cost_credits=spec["cost_credits"],
                )
            )

    # --- Default preset: create new version with updated blocks if needed ---
    presets_tbl = sa.table(
        "presets",
        sa.column("preset_id", sa.Uuid),
        sa.column("business_type_key", sa.String),
        sa.column("name", sa.String),
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

    blocks_payload = DEFAULT_PRESET_BLOCKS
    content_hash = hashlib.sha256(
        json.dumps(blocks_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()

    # Find all default presets
    default_presets = conn.execute(
        sa.select(presets_tbl.c.preset_id, presets_tbl.c.business_type_key).where(
            presets_tbl.c.name == DEFAULT_PRESET_NAME
        )
    ).all()

    for preset_id, btype_key in default_presets:
        # Check if active version already has this content_hash (idempotent)
        existing_active = conn.execute(
            sa.select(preset_versions_tbl.c.version_id, preset_versions_tbl.c.content_hash)
            .where(
                preset_versions_tbl.c.preset_id == preset_id,
                preset_versions_tbl.c.status == "ACTIVE",
            )
            .order_by(preset_versions_tbl.c.version.desc())
            .limit(1)
        ).first()
        if existing_active and existing_active[1] == content_hash:
            continue  # Already up-to-date

        # Supersede old active
        if existing_active:
            conn.execute(
                preset_versions_tbl.update()
                .where(preset_versions_tbl.c.version_id == existing_active[0])
                .values(status="SUPERSEDED")
            )
            # Get next version number
            max_ver = conn.execute(
                sa.select(sa.func.max(preset_versions_tbl.c.version)).where(
                    preset_versions_tbl.c.preset_id == preset_id
                )
            ).scalar() or 0
            next_ver = max_ver + 1
        else:
            next_ver = 1

        conn.execute(
            preset_versions_tbl.insert().values(
                version_id=uuid.uuid4(),
                preset_id=preset_id,
                version=next_ver,
                blocks=blocks_payload,
                content_hash=content_hash,
                status="ACTIVE",
                created_by=None,
            )
        )

    # Also handle case where no preset exists yet (fresh business types created after seed)
    # This will be handled by seed_reference_data on next run, but we also ensure at least general exists
    # via seed logic — no need to create new presets here for missing business types.

def downgrade() -> None:
    # Revert preset blocks to previous simple version is not trivial; we leave AI defs as-is on downgrade
    # but we could delete the new AI definitions (except ai_description)
    conn = op.get_bind()
    ai_defs_tbl = sa.table(
        "ai_output_definitions",
        sa.column("key", sa.String),
        sa.column("version", sa.Integer),
    )
    for key in ["ai_features", "ai_games", "ai_software", "ai_hashtags"]:
        conn.execute(
            sa.delete(ai_defs_tbl).where(
                ai_defs_tbl.c.key == key,
                ai_defs_tbl.c.version == 1,
            )
        )
