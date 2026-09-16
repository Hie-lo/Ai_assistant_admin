"""Phase 2: subscription / payment / entitlement

Revision ID: f0e1d2c3b4a5
Revises: a1b2c4d5e5f6
Create Date: 2026-09-13

Adds the commercial layer: plan catalog, subscription lifecycle, manual
payment records, and the AI credit ledger (separate monthly/purchased
pools + append-only transactions). Also adds ``users.is_super_admin``
(platform operator flag). Seeds the starter plan (owner-approved
placeholder values, tunable at runtime without code changes).

Backstop invariants (Postgres partial unique indexes):
- at most one non-terminal subscription per business
- at most one PENDING payment per subscription

Additive only; rollback drops the new tables and column in dependency
order.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f0e1d2c3b4a5"
down_revision: str | None = "a1b2c4d5e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _sub_status() -> sa.Enum:
    return sa.Enum(
        "PENDING", "ACTIVE", "GRACE", "EXPIRED", "SUSPENDED", "CANCELLED", "REFUNDED",
        name="subscriptionstatus",
        native_enum=False,
    )


def _payment_status() -> sa.Enum:
    return sa.Enum("PENDING", "VERIFIED", "REJECTED", name="paymentstatus", native_enum=False)


def _payment_purpose() -> sa.Enum:
    return sa.Enum("NEW", "RENEWAL", "PLAN_CHANGE", name="paymentpurpose", native_enum=False)


def _pool_type() -> sa.Enum:
    return sa.Enum("MONTHLY", "PURCHASED", name="creditpooltype", native_enum=False)


def _credit_direction() -> sa.Enum:
    return sa.Enum(
        "GRANT", "CONSUME", "REFUND", "EXPIRE", name="creditdirection", native_enum=False
    )


def upgrade() -> None:
    # --- users: platform operator flag ---
    op.add_column(
        "users",
        sa.Column(
            "is_super_admin",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    # --- plans ---
    op.create_table(
        "plans",
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="IRT"),
        sa.Column("price", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "billing_period", sa.String(length=16), nullable=False, server_default="monthly"
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("product_limit", sa.Integer(), nullable=True),
        sa.Column("source_limit", sa.Integer(), nullable=True),
        sa.Column("channel_limit", sa.Integer(), nullable=True),
        sa.Column("sync_frequency_per_day", sa.Integer(), nullable=True),
        sa.Column("ai_available", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("ai_monthly_credits", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "preset_customization", sa.String(length=16), nullable=False, server_default="none"
        ),
        sa.Column("report_level", sa.String(length=16), nullable=False, server_default="none"),
        sa.Column("media_storage_limit_bytes", sa.Integer(), nullable=True),
        sa.Column("admin_seat_limit", sa.Integer(), nullable=True),
        sa.Column("feature_flags", sa.JSON(), nullable=False),
        sa.Column(

            "created_at", sa.DateTime(timezone=True),

            server_default=sa.text("CURRENT_TIMESTAMP"),

            nullable=False,

        ),
        sa.Column(

            "updated_at", sa.DateTime(timezone=True),

            server_default=sa.text("CURRENT_TIMESTAMP"),

            nullable=False,

        ),
        sa.PrimaryKeyConstraint("plan_id"),
        sa.UniqueConstraint("code"),
    )
    op.create_index("ix_plans_code", "plans", ["code"], unique=True)

    # --- subscriptions ---
    op.create_table(
        "subscriptions",
        sa.Column("subscription_id", sa.Uuid(), nullable=False),
        sa.Column(
            "business_id",
            sa.Uuid(),
            sa.ForeignKey("businesses.business_id"),
            nullable=False,
        ),
        sa.Column("plan_id", sa.Uuid(), sa.ForeignKey("plans.plan_id"), nullable=False),
        sa.Column("status", _sub_status(), nullable=False, server_default="PENDING"),
        sa.Column("pending_plan_id", sa.Uuid(), sa.ForeignKey("plans.plan_id"), nullable=True),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("grace_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("updated_by", sa.Uuid(), sa.ForeignKey("users.user_id"), nullable=True),
        sa.Column(

            "created_at", sa.DateTime(timezone=True),

            server_default=sa.text("CURRENT_TIMESTAMP"),

            nullable=False,

        ),
        sa.Column(

            "updated_at", sa.DateTime(timezone=True),

            server_default=sa.text("CURRENT_TIMESTAMP"),

            nullable=False,

        ),
        sa.PrimaryKeyConstraint("subscription_id"),
    )
    op.create_index("ix_subscriptions_business_id", "subscriptions", ["business_id"])
    # Backstop: one live subscription per business.
    op.create_index(
        "uq_non_terminal_subscription",
        "subscriptions",
        ["business_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('PENDING', 'ACTIVE', 'GRACE', 'SUSPENDED')"
        ),
    )

    # --- payments ---
    op.create_table(
        "payments",
        sa.Column("payment_id", sa.Uuid(), nullable=False),
        sa.Column(
            "subscription_id",
            sa.Uuid(),
            sa.ForeignKey("subscriptions.subscription_id"),
            nullable=False,
        ),
        sa.Column(
            "business_id",
            sa.Uuid(),
            sa.ForeignKey("businesses.business_id"),
            nullable=False,
        ),
        sa.Column("purpose", _payment_purpose(), nullable=False, server_default="NEW"),
        sa.Column("status", _payment_status(), nullable=False, server_default="PENDING"),
        sa.Column("expected_amount", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="IRT"),
        sa.Column("amount_paid", sa.Integer(), nullable=True),
        sa.Column("reference", sa.String(length=120), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("initiated_by", sa.Uuid(), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("verified_by", sa.Uuid(), sa.ForeignKey("users.user_id"), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(

            "created_at", sa.DateTime(timezone=True),

            server_default=sa.text("CURRENT_TIMESTAMP"),

            nullable=False,

        ),
        sa.PrimaryKeyConstraint("payment_id"),
    )
    op.create_index("ix_payments_subscription_id", "payments", ["subscription_id"])
    op.create_index("ix_payments_business_id", "payments", ["business_id"])
    # Backstop: one pending payment per subscription.
    op.create_index(
        "uq_pending_payment",
        "payments",
        ["subscription_id"],
        unique=True,
        postgresql_where=sa.text("status = 'PENDING'"),
    )

    # --- credit_pools ---
    op.create_table(
        "credit_pools",
        sa.Column("pool_id", sa.Uuid(), nullable=False),
        sa.Column(
            "business_id",
            sa.Uuid(),
            sa.ForeignKey("businesses.business_id"),
            nullable=False,
        ),
        sa.Column("pool_type", _pool_type(), nullable=False),
        sa.Column("period_label", sa.String(length=16), nullable=False),
        sa.Column("granted_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("remaining", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(

            "created_at", sa.DateTime(timezone=True),

            server_default=sa.text("CURRENT_TIMESTAMP"),

            nullable=False,

        ),
        sa.Column(

            "updated_at", sa.DateTime(timezone=True),

            server_default=sa.text("CURRENT_TIMESTAMP"),

            nullable=False,

        ),
        sa.PrimaryKeyConstraint("pool_id"),
        sa.UniqueConstraint("business_id", "pool_type", "period_label", name="uq_credit_pool"),
    )
    op.create_index("ix_credit_pools_business_id", "credit_pools", ["business_id"])

    # --- credit_transactions ---
    op.create_table(
        "credit_transactions",
        sa.Column("tx_id", sa.Uuid(), nullable=False),
        sa.Column(
            "pool_id", sa.Uuid(), sa.ForeignKey("credit_pools.pool_id"), nullable=False
        ),
        sa.Column(
            "business_id",
            sa.Uuid(),
            sa.ForeignKey("businesses.business_id"),
            nullable=False,
        ),
        sa.Column("direction", _credit_direction(), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=120), nullable=True),
        sa.Column("reference", sa.String(length=120), nullable=True),
        sa.Column(
            "actor_user_id", sa.Uuid(), sa.ForeignKey("users.user_id"), nullable=True
        ),
        sa.Column("meta_data", sa.JSON(), nullable=True),
        sa.Column(

            "created_at", sa.DateTime(timezone=True),

            server_default=sa.text("CURRENT_TIMESTAMP"),

            nullable=False,

        ),
        sa.PrimaryKeyConstraint("tx_id"),
        sa.UniqueConstraint("idempotency_key", name="uq_credit_tx_idempotency"),
    )
    op.create_index("ix_credit_transactions_pool_id", "credit_transactions", ["pool_id"])
    op.create_index(
        "ix_credit_transactions_business_id", "credit_transactions", ["business_id"]
    )
    op.create_index(
        "ix_credit_transactions_idempotency_key", "credit_transactions", ["idempotency_key"]
    )
    op.create_index("ix_credit_transactions_reference", "credit_transactions", ["reference"])

    # --- seed: starter plan (values from app.infrastructure.db.seed) ---
    import uuid  # noqa: PLC0415

    from app.infrastructure.db.seed import STARTER_PLAN  # noqa: PLC0415

    op.bulk_insert(
        sa.table(
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
            sa.column("preset_customization", sa.String),
            sa.column("report_level", sa.String),
            sa.column("media_storage_limit_bytes", sa.Integer),
            sa.column("admin_seat_limit", sa.Integer),
            sa.column("feature_flags", sa.JSON),
        ),
        [
            {
                "plan_id": uuid.uuid4(),
                "code": STARTER_PLAN["code"],
                "name": STARTER_PLAN["name"],
                "currency": STARTER_PLAN["currency"],
                "price": STARTER_PLAN["price"],
                "billing_period": STARTER_PLAN["billing_period"],
                "is_active": STARTER_PLAN["is_active"],
                "product_limit": STARTER_PLAN["product_limit"],
                "source_limit": STARTER_PLAN["source_limit"],
                "channel_limit": STARTER_PLAN["channel_limit"],
                "sync_frequency_per_day": STARTER_PLAN["sync_frequency_per_day"],
                "ai_available": STARTER_PLAN["ai_available"],
                "ai_monthly_credits": STARTER_PLAN["ai_monthly_credits"],
                "preset_customization": STARTER_PLAN["preset_customization"],
                "report_level": STARTER_PLAN["report_level"],
                "media_storage_limit_bytes": STARTER_PLAN["media_storage_limit_bytes"],
                "admin_seat_limit": STARTER_PLAN["admin_seat_limit"],
                "feature_flags": STARTER_PLAN["feature_flags"],
            }
        ],
    )


def downgrade() -> None:
    op.drop_table("credit_transactions")
    op.drop_table("credit_pools")
    op.drop_table("payments")
    op.drop_index("uq_non_terminal_subscription", table_name="subscriptions")
    op.drop_index("ix_subscriptions_business_id", table_name="subscriptions")
    op.drop_table("subscriptions")
    op.drop_index("ix_plans_code", table_name="plans")
    op.drop_table("plans")
    op.drop_column("users", "is_super_admin")
