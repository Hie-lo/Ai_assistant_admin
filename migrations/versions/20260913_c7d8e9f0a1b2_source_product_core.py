"""Phase 3: source / product core

Revision ID: c7d8e9f0a1b2
Revises: f0e1d2c3b4a5
Create Date: 2026-09-13

Adds the product-ingestion core (PRODUCT_DOMAIN_SPECIFICATION_V2 +
SOURCE_SYNC_DOMAIN_SPECIFICATION_V1):

- ``sources``: connected product sources (Excel upload / Google Sheets).
- ``source_mappings``: versioned column mapping (DRAFT/ACTIVE/SUPERSEDED).
- ``products``: canonical products with durable UUID identity, typed
  custom attributes (JSON), fingerprint, and lifecycle state.
- ``product_versions``: compact version rows (change categories + risk).
- ``product_media``: first-class media items with origin protection
  (SOURCE vs CUSTOMER).
- ``source_records``: per-source row locators linking reads to products.
- ``review_cases``: quarantined, explainable human-decision cases.
- ``import_runs``: one manual import/sync execution + summary counts.

Backstop invariants (unique indexes):
- one locator per (source) — row position is unique within a source
- one version_no per (product)

Additive only; rollback drops the new tables in dependency order.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7d8e9f0a1b2"
down_revision: str | None = "f0e1d2c3b4a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _source_kind() -> sa.Enum:
    return sa.Enum(
        "EXCEL_UPLOAD", "GOOGLE_SHEETS", name="sourcekind", native_enum=False
    )


def _source_status() -> sa.Enum:
    return sa.Enum(
        "PENDING_MAPPING", "ACTIVE", "PAUSED", "ERROR",
        name="sourcestatus",
        native_enum=False,
    )


def _mapping_status() -> sa.Enum:
    return sa.Enum("DRAFT", "ACTIVE", "SUPERSEDED", name="mappingstatus", native_enum=False)


def _product_lifecycle() -> sa.Enum:
    return sa.Enum(
        "ACTIVE",
        "MISSING_FROM_SOURCE",
        "REVIEW_REQUIRED",
        "ARCHIVED",
        "DISCOVERED",
        name="productlifecycle",
        native_enum=False,
    )


def _change_risk() -> sa.Enum:
    return sa.Enum("LOW", "MEDIUM", "HIGH", "CRITICAL", name="changerisk", native_enum=False)


def _media_origin() -> sa.Enum:
    return sa.Enum("SOURCE", "CUSTOMER", name="mediaorigin", native_enum=False)


def _media_status() -> sa.Enum:
    return sa.Enum("ACTIVE", "REMOVED", name="mediastatus", native_enum=False)


def _review_case_kind() -> sa.Enum:
    return sa.Enum(
        "IDENTITY_AMBIGUOUS",
        "IDENTITY_CONFLICT",
        "DUPLICATE_CANDIDATE",
        "SUSPICIOUS_CHANGE",
        "MASS_MISSING_BLOCKED",
        name="reviewcasekind",
        native_enum=False,
    )


def _review_case_status() -> sa.Enum:
    return sa.Enum("OPEN", "RESOLVED", "DISMISSED", name="reviewcasestatus", native_enum=False)


def _sync_trigger() -> sa.Enum:
    return sa.Enum("MANUAL", name="synctrigger", native_enum=False)


def _import_run_status() -> sa.Enum:
    return sa.Enum(
        "RUNNING",
        "SUCCEEDED",
        "SUCCEEDED_WITH_ERRORS",
        "FAILED",
        "PREVIEW",
        name="importrunstatus",
        native_enum=False,
    )


def upgrade() -> None:
    # --- sources ---
    op.create_table(
        "sources",
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column(
            "business_id", sa.Uuid(), sa.ForeignKey("businesses.business_id"), nullable=False
        ),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("kind", _source_kind(), nullable=False),
        sa.Column("external_ref", sa.String(length=320), nullable=True),
        sa.Column("sheet_name", sa.String(length=120), nullable=True),
        sa.Column("range_spec", sa.String(length=120), nullable=True),
        sa.Column(
            "status",
            _source_status(),
            nullable=False,
            server_default="PENDING_MAPPING",
        ),
        sa.Column("credentials_ref", sa.String(length=120), nullable=True),
        sa.Column(
            "media_authoritative", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_baseline_count", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False,
        ),
        sa.PrimaryKeyConstraint("source_id"),
    )
    op.create_index("ix_sources_business_id", "sources", ["business_id"])

    # --- products ---
    op.create_table(
        "products",
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column(
            "business_id", sa.Uuid(), sa.ForeignKey("businesses.business_id"), nullable=False
        ),
        sa.Column("name", sa.String(length=320), nullable=False),
        sa.Column("category", sa.String(length=160), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("price", sa.Integer(), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="IRT"),
        sa.Column("stock", sa.Integer(), nullable=True),
        sa.Column("external_id", sa.String(length=120), nullable=True),
        sa.Column("sku", sa.String(length=120), nullable=True),
        sa.Column("barcode", sa.String(length=120), nullable=True),
        sa.Column("fingerprint", sa.String(length=64), nullable=True),
        sa.Column(
            "lifecycle_state",
            _product_lifecycle(),
            nullable=False,
            server_default="ACTIVE",
        ),
        sa.Column("current_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("attributes", sa.JSON(), nullable=False),
        sa.Column("identity_evidence", sa.JSON(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False,
        ),
        sa.PrimaryKeyConstraint("product_id"),
    )
    op.create_index("ix_products_business_id", "products", ["business_id"])
    op.create_index("ix_products_external_id", "products", ["external_id"])
    op.create_index("ix_products_sku", "products", ["sku"])
    op.create_index("ix_products_barcode", "products", ["barcode"])
    op.create_index("ix_products_fingerprint", "products", ["fingerprint"])

    # --- source_mappings (versioned) ---
    op.create_table(
        "source_mappings",
        sa.Column("mapping_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), sa.ForeignKey("sources.source_id"), nullable=False),
        sa.Column(
            "business_id", sa.Uuid(), sa.ForeignKey("businesses.business_id"), nullable=False
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", _mapping_status(), nullable=False, server_default="DRAFT"),
        sa.Column("entries", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.user_id"), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False,
        ),
        sa.PrimaryKeyConstraint("mapping_id"),
    )
    op.create_index("ix_source_mappings_source_id", "source_mappings", ["source_id"])
    op.create_index("ix_source_mappings_business_id", "source_mappings", ["business_id"])

    # --- source_records (locator, not identity) ---
    op.create_table(
        "source_records",
        sa.Column("record_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), sa.ForeignKey("sources.source_id"), nullable=False),
        sa.Column(
            "business_id", sa.Uuid(), sa.ForeignKey("businesses.business_id"), nullable=False
        ),
        sa.Column("locator", sa.String(length=220), nullable=False),
        sa.Column("external_key", sa.String(length=120), nullable=True),
        sa.Column("sku", sa.String(length=120), nullable=True),
        sa.Column("barcode", sa.String(length=120), nullable=True),
        sa.Column("fingerprint", sa.String(length=64), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("products.product_id"), nullable=True),
        sa.Column(
            "mapping_version_id",
            sa.Uuid(),
            sa.ForeignKey("source_mappings.mapping_id"),
            nullable=True,
        ),
        sa.Column("state", sa.String(length=32), nullable=False, server_default="PRESENT"),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False,
        ),
        sa.PrimaryKeyConstraint("record_id"),
    )
    op.create_index("ix_source_records_source_id", "source_records", ["source_id"])
    op.create_index("ix_source_records_business_id", "source_records", ["business_id"])
    op.create_index("ix_source_records_product_id", "source_records", ["product_id"])
    op.create_index("ix_source_records_external_key", "source_records", ["external_key"])
    op.create_index("ix_source_records_sku", "source_records", ["sku"])
    op.create_index("ix_source_records_barcode", "source_records", ["barcode"])
    op.create_index("ix_source_records_fingerprint", "source_records", ["fingerprint"])
    op.create_index("ix_source_records_content_hash", "source_records", ["content_hash"])
    # Backstop: one locator per source.
    op.create_index(
        "uq_source_record_locator", "source_records", ["source_id", "locator"], unique=True
    )

    # --- product_versions (compact) ---
    op.create_table(
        "product_versions",
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("products.product_id"), nullable=False),
        sa.Column(
            "business_id", sa.Uuid(), sa.ForeignKey("businesses.business_id"), nullable=False
        ),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("change_categories", sa.JSON(), nullable=False),
        sa.Column("risk_level", _change_risk(), nullable=False),
        sa.Column("changed_fields", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "mapping_version_id",
            sa.Uuid(),
            sa.ForeignKey("source_mappings.mapping_id"),
            nullable=True,
        ),
        sa.Column("trigger", _sync_trigger(), nullable=False, server_default="MANUAL"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False,
        ),
        sa.PrimaryKeyConstraint("version_id"),
    )
    op.create_index("ix_product_versions_product_id", "product_versions", ["product_id"])
    op.create_index("ix_product_versions_business_id", "product_versions", ["business_id"])
    # Backstop: one version_no per product.
    op.create_index(
        "uq_product_version_no", "product_versions", ["product_id", "version_no"], unique=True
    )

    # --- product_media ---
    op.create_table(
        "product_media",
        sa.Column("media_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("products.product_id"), nullable=False),
        sa.Column(
            "business_id", sa.Uuid(), sa.ForeignKey("businesses.business_id"), nullable=False
        ),
        sa.Column("media_type", sa.String(length=24), nullable=False, server_default="IMAGE"),
        sa.Column("origin", _media_origin(), nullable=False),
        sa.Column("url", sa.String(length=1024), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fingerprint", sa.String(length=64), nullable=True),
        sa.Column("status", _media_status(), nullable=False, server_default="ACTIVE"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False,
        ),
        sa.PrimaryKeyConstraint("media_id"),
    )
    op.create_index("ix_product_media_product_id", "product_media", ["product_id"])
    op.create_index("ix_product_media_business_id", "product_media", ["business_id"])

    # --- review_cases ---
    op.create_table(
        "review_cases",
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column(
            "business_id", sa.Uuid(), sa.ForeignKey("businesses.business_id"), nullable=False
        ),
        sa.Column("kind", _review_case_kind(), nullable=False),
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("products.product_id"), nullable=True),
        sa.Column("source_id", sa.Uuid(), sa.ForeignKey("sources.source_id"), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", _review_case_status(), nullable=False, server_default="OPEN"),
        sa.Column("resolution", sa.String(length=80), nullable=True),
        sa.Column("resolved_by", sa.Uuid(), sa.ForeignKey("users.user_id"), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False,
        ),
        sa.PrimaryKeyConstraint("case_id"),
    )
    op.create_index("ix_review_cases_business_id", "review_cases", ["business_id"])
    op.create_index("ix_review_cases_product_id", "review_cases", ["product_id"])
    op.create_index("ix_review_cases_source_id", "review_cases", ["source_id"])

    # --- import_runs ---
    op.create_table(
        "import_runs",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), sa.ForeignKey("sources.source_id"), nullable=False),
        sa.Column(
            "business_id", sa.Uuid(), sa.ForeignKey("businesses.business_id"), nullable=False
        ),
        sa.Column(
            "mapping_version_id",
            sa.Uuid(),
            sa.ForeignKey("source_mappings.mapping_id"),
            nullable=True,
        ),
        sa.Column("trigger", _sync_trigger(), nullable=False, server_default="MANUAL"),
        sa.Column("status", _import_run_status(), nullable=False, server_default="RUNNING"),
        sa.Column("requested_by", sa.Uuid(), sa.ForeignKey("users.user_id"), nullable=True),
        sa.Column("counts", sa.JSON(), nullable=False),
        sa.Column("row_errors", sa.JSON(), nullable=False),
        sa.Column("failure_summary", sa.Text(), nullable=True),
        sa.Column("correlation_id", sa.String(length=64), nullable=False),
        sa.Column(
            "started_at", sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("run_id"),
    )
    op.create_index("ix_import_runs_source_id", "import_runs", ["source_id"])
    op.create_index("ix_import_runs_business_id", "import_runs", ["business_id"])


def downgrade() -> None:
    op.drop_table("import_runs")
    op.drop_table("review_cases")
    op.drop_table("product_media")
    op.drop_index("uq_product_version_no", table_name="product_versions")
    op.drop_index("ix_product_versions_product_id", table_name="product_versions")
    op.drop_index("ix_product_versions_business_id", table_name="product_versions")
    op.drop_table("product_versions")
    op.drop_index("uq_source_record_locator", table_name="source_records")
    op.drop_index("ix_source_records_source_id", table_name="source_records")
    op.drop_index("ix_source_records_business_id", table_name="source_records")
    op.drop_index("ix_source_records_product_id", table_name="source_records")
    op.drop_index("ix_source_records_external_key", table_name="source_records")
    op.drop_index("ix_source_records_sku", table_name="source_records")
    op.drop_index("ix_source_records_barcode", table_name="source_records")
    op.drop_index("ix_source_records_fingerprint", table_name="source_records")
    op.drop_index("ix_source_records_content_hash", table_name="source_records")
    op.drop_table("source_records")
    op.drop_index("ix_source_mappings_source_id", table_name="source_mappings")
    op.drop_index("ix_source_mappings_business_id", table_name="source_mappings")
    op.drop_table("source_mappings")
    op.drop_index("ix_products_business_id", table_name="products")
    op.drop_index("ix_products_external_id", table_name="products")
    op.drop_index("ix_products_sku", table_name="products")
    op.drop_index("ix_products_barcode", table_name="products")
    op.drop_index("ix_products_fingerprint", table_name="products")
    op.drop_table("products")
    op.drop_index("ix_sources_business_id", table_name="sources")
    op.drop_table("sources")
