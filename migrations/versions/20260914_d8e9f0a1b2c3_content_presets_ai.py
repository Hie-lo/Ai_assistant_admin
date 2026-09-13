"""Phase 4: content / presets / AI output registry

Revision ID: d8e9f0a1b2c3
Revises: c7d8e9f0a1b2
Create Date: 2026-09-14

Adds the content domain (MASTER_PROJECT_SPECIFICATION sections 9-10,
POST_PUBLICATION_DOMAIN_SPECIFICATION sections 4-5/7/10/23,
AI_CONFIGURATION_SPECIFICATION_V1):

- ``presets`` / ``preset_versions``: versioned, platform-neutral
  business-type presets (structured blocks; historical versions never
  mutated). Seeded default preset per business type.
- ``product_presets`` / ``product_preset_versions``: per-product presets —
  a COMPLETELY separate section from business-type presets (owner
  decision 2026-09-13), top-plan entitlement only.
- ``ai_output_definitions``: configurable, versioned AI output
  definitions (key/display name/prompt template/inputs/max length/cost).
  Seeded ai_description + ai_short_title.
- ``ai_output_artifacts``: generated/approved AI outputs bound to a
  product + definition version + source dependency fingerprint (reuse
  rule; prompt/model changes never auto-regenerate).
- ``businesses.ai_automatic_enabled``: automatic-mode intent (trigger
  lands in Phase 8).
- ``plans.product_preset_eligible``: top-plan entitlement flag for
  per-product presets (operator-managed at runtime).
- ``products.product_preset_id``: optional per-product preset assignment.

Additive only; rollback drops the new columns/tables in dependency order.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d8e9f0a1b2c3"
down_revision: str | None = "c7d8e9f0a1b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _preset_version_status() -> sa.Enum:
    return sa.Enum(
        "DRAFT", "ACTIVE", "SUPERSEDED", name="presetversionstatus", native_enum=False
    )


def _ai_artifact_status() -> sa.Enum:
    return sa.Enum(
        "PENDING_APPROVAL", "APPROVED", "REJECTED", "SUPERSEDED",
        name="aiartifactstatus",
        native_enum=False,
    )


def upgrade() -> None:
    # --- business-type presets ---
    op.create_table(
        "presets",
        sa.Column("preset_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "business_type_key",
            sa.String(64),
            sa.ForeignKey("business_types.key"),
            nullable=False,
        ),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.user_id")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("business_type_key", "name", name="uq_presets_type_name"),
    )
    op.create_index("ix_presets_business_type_key", "presets", ["business_type_key"])

    op.create_table(
        "preset_versions",
        sa.Column("version_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "preset_id",
            sa.Uuid(),
            sa.ForeignKey("presets.preset_id"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("blocks", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("status", _preset_version_status(), nullable=False),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.user_id")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("preset_id", "version", name="uq_preset_versions_no"),
    )
    op.create_index("ix_preset_versions_preset_id", "preset_versions", ["preset_id"])

    # --- per-product presets (separate section, top-plan entitlement) ---
    op.create_table(
        "product_presets",
        sa.Column("product_preset_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "business_id",
            sa.Uuid(),
            sa.ForeignKey("businesses.business_id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.user_id")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("business_id", "name", name="uq_product_presets_name"),
    )
    op.create_index("ix_product_presets_business_id", "product_presets", ["business_id"])

    op.create_table(
        "product_preset_versions",
        sa.Column("version_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "product_preset_id",
            sa.Uuid(),
            sa.ForeignKey("product_presets.product_preset_id"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("blocks", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("status", _preset_version_status(), nullable=False),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.user_id")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "product_preset_id", "version", name="uq_ppreset_versions_no"
        ),
    )
    op.create_index(
        "ix_product_preset_versions_ppreset_id",
        "product_preset_versions",
        ["product_preset_id"],
    )

    # --- AI output definitions + artifacts ---
    op.create_table(
        "ai_output_definitions",
        sa.Column("definition_id", sa.Uuid(), primary_key=True),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("prompt_template", sa.Text(), nullable=False),
        sa.Column("input_fields", sa.JSON(), nullable=False),
        sa.Column(
            "max_output_length", sa.Integer(), nullable=False, server_default="800"
        ),
        sa.Column("provider_policy", sa.String(64)),
        sa.Column("cost_credits", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("retry_policy", sa.String(64)),
        sa.Column("allowed_contexts", sa.JSON()),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.user_id")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("key", "version", name="uq_ai_defs_key_version"),
    )
    op.create_index("ix_ai_output_definitions_key", "ai_output_definitions", ["key"])

    op.create_table(
        "ai_output_artifacts",
        sa.Column("artifact_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "product_id",
            sa.Uuid(),
            sa.ForeignKey("products.product_id"),
            nullable=False,
        ),
        sa.Column(
            "business_id",
            sa.Uuid(),
            sa.ForeignKey("businesses.business_id"),
            nullable=False,
        ),
        sa.Column(
            "output_definition_id",
            sa.Uuid(),
            sa.ForeignKey("ai_output_definitions.definition_id"),
        ),
        sa.Column("output_definition_key", sa.String(64), nullable=False),
        sa.Column("output_definition_version", sa.Integer(), nullable=False),
        sa.Column("source_dependency_fingerprint", sa.String(64), nullable=False),
        sa.Column("generated_value", sa.Text(), nullable=False),
        sa.Column("approved_value", sa.Text()),
        sa.Column("status", _ai_artifact_status(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True)),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("approved_by", sa.Uuid(), sa.ForeignKey("users.user_id")),
        sa.Column("model", sa.String(120)),
        sa.Column("provider", sa.String(48)),
        sa.Column("prompt_chars", sa.Integer()),
        sa.Column("completion_chars", sa.Integer()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_ai_output_artifacts_product_id", "ai_output_artifacts", ["product_id"]
    )
    op.create_index(
        "ix_ai_output_artifacts_business_id", "ai_output_artifacts", ["business_id"]
    )

    # --- new columns ---
    op.add_column(
        "businesses",
        sa.Column(
            "ai_automatic_enabled",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )
    op.add_column(
        "plans",
        sa.Column(
            "product_preset_eligible",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )
    op.add_column(
        "products",
        sa.Column(
            "product_preset_id",
            sa.Uuid(),
            sa.ForeignKey("product_presets.product_preset_id"),
        ),
    )
    op.create_index(
        "ix_products_product_preset_id", "products", ["product_preset_id"]
    )

    # --- seed: AI definitions + default preset (mirrors seed.py) ---
    import hashlib as _hashlib
    import json as _jsonlib
    import uuid as _uuid

    conn = op.get_bind()

    from app.infrastructure.db.seed import (  # noqa: PLC0415
        AI_DEFINITION_SEED,
        DEFAULT_PRESET_BLOCKS,
        DEFAULT_PRESET_NAME,
    )

    ai_defs_tbl = sa.table(
        "ai_output_definitions",
        sa.column("key", sa.String),
        sa.column("version", sa.Integer),
    )
    for spec in AI_DEFINITION_SEED:
        existing = conn.execute(
            sa.select(1)
            .select_from(ai_defs_tbl)
            .where(
                ai_defs_tbl.c.key == spec["key"],
                ai_defs_tbl.c.version == spec["version"],
            )
        ).first()
        if existing is None:
            op.bulk_insert(
                sa.table(
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
                    sa.column("created_at", sa.DateTime(timezone=True)),
                ),
                [
                    {
                        "definition_id": _uuid.uuid4(),
                        "key": spec["key"],
                        "version": spec["version"],
                        "display_name": spec["display_name"],
                        "prompt_template": spec["prompt_template"],
                        "input_fields": spec["input_fields"],
                        "max_output_length": spec["max_output_length"],
                        "provider_policy": spec["provider_policy"],
                        "cost_credits": spec["cost_credits"],
                        "retry_policy": spec["retry_policy"],
                        "allowed_contexts": spec["allowed_contexts"],
                        "active": spec["active"],
                        "created_by": None,
                        "created_at": None,
                    }
                ],
            )

    blocks_payload = [
        {
            "id": b["id"],
            "type": b["type"],
            "ownership": b["ownership"],
            "payload": b["payload"],
        }
        for b in DEFAULT_PRESET_BLOCKS
    ]
    content_hash = _hashlib.sha256(
        _jsonlib.dumps(blocks_payload, ensure_ascii=False, sort_keys=True).encode(
            "utf-8"
        )
    ).hexdigest()
    presets_tbl = sa.table(
        "presets",
        sa.column("preset_id", sa.Uuid),
        sa.column("business_type_key", sa.String),
        sa.column("name", sa.String),
    )
    btype_keys = [
        row[0]
        for row in conn.execute(
            sa.select(sa.column("key")).select_from(
                sa.table("business_types", sa.column("key", sa.String))
            )
        ).all()
    ]
    for btype_key in btype_keys:
        has_preset = conn.execute(
            sa.select(1)
            .select_from(presets_tbl)
            .where(
                presets_tbl.c.business_type_key == btype_key,
                presets_tbl.c.name == DEFAULT_PRESET_NAME,
            )
        ).first()
        if has_preset is None:
            preset_id = _uuid.uuid4()
            op.bulk_insert(
                sa.table(
                    "presets",
                    sa.column("preset_id", sa.Uuid),
                    sa.column("business_type_key", sa.String),
                    sa.column("name", sa.String),
                    sa.column("description", sa.Text),
                    sa.column("is_default", sa.Boolean),
                    sa.column("created_by", sa.Uuid),
                    sa.column("created_at", sa.DateTime(timezone=True)),
                    sa.column("updated_at", sa.DateTime(timezone=True)),
                ),
                [
                    {
                        "preset_id": preset_id,
                        "business_type_key": btype_key,
                        "name": DEFAULT_PRESET_NAME,
                        "description": "Default preset for this business type.",
                        "is_default": True,
                        "created_by": None,
                        "created_at": None,
                        "updated_at": None,
                    }
                ],
            )
            op.bulk_insert(
                sa.table(
                    "preset_versions",
                    sa.column("version_id", sa.Uuid),
                    sa.column("preset_id", sa.Uuid),
                    sa.column("version", sa.Integer),
                    sa.column("blocks", sa.JSON),
                    sa.column("content_hash", sa.String),
                    sa.column("status", sa.String),
                    sa.column("created_by", sa.Uuid),
                    sa.column("created_at", sa.DateTime(timezone=True)),
                ),
                [
                    {
                        "version_id": _uuid.uuid4(),
                        "preset_id": preset_id,
                        "version": 1,
                        "blocks": blocks_payload,
                        "content_hash": content_hash,
                        "status": "ACTIVE",
                        "created_by": None,
                        "created_at": None,
                    }
                ],
            )


def downgrade() -> None:
    op.drop_index("ix_products_product_preset_id", table_name="products")
    op.drop_column("products", "product_preset_id")
    op.drop_column("plans", "product_preset_eligible")
    op.drop_column("businesses", "ai_automatic_enabled")
    op.drop_index("ix_ai_output_artifacts_business_id", table_name="ai_output_artifacts")
    op.drop_index("ix_ai_output_artifacts_product_id", table_name="ai_output_artifacts")
    op.drop_table("ai_output_artifacts")
    op.drop_index("ix_ai_output_definitions_key", table_name="ai_output_definitions")
    op.drop_table("ai_output_definitions")
    op.drop_index(
        "ix_product_preset_versions_ppreset_id", table_name="product_preset_versions"
    )
    op.drop_table("product_preset_versions")
    op.drop_index("ix_product_presets_business_id", table_name="product_presets")
    op.drop_table("product_presets")
    op.drop_index("ix_preset_versions_preset_id", table_name="preset_versions")
    op.drop_table("preset_versions")
    op.drop_index("ix_presets_business_type_key", table_name="presets")
    op.drop_table("presets")
