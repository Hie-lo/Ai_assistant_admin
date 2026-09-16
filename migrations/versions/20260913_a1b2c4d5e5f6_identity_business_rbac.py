"""Phase 1: identity, business, membership/RBAC foundation

Revision ID: a1b2c3d4e5f6
Revises:
Create Date: 2026-09-13

Adds the identity/business/RBAC tables and seeds the initial business type
('general'). This migration is additive only (no data reinterpretation).
Rollback drops all tables added here in dependency order.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c4d5e5f6"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _account_status() -> sa.Enum:
    return sa.Enum("ACTIVE", "SUSPENDED", "DELETED", name="accountstatus", native_enum=False)


def _lifecycle() -> sa.Enum:
    return sa.Enum("ACTIVE", "SUSPENDED", "ARCHIVED", name="businesslifecycle", native_enum=False)


def _role() -> sa.Enum:
    return sa.Enum("OWNER", "ADMIN", name="membershiprole", native_enum=False)


def _membership_status() -> sa.Enum:
    return sa.Enum(
        "PENDING_REQUEST", "ACTIVE", "REJECTED", "REVOKED", "SUSPENDED",
        name="membershipstatus",
        native_enum=False,
    )


def _request_method() -> sa.Enum:
    return sa.Enum("INVITE_CODE", "CHANNEL_REF", name="adminrequestmethod", native_enum=False)


def _request_status() -> sa.Enum:
    return sa.Enum(
        "PENDING", "APPROVED", "REJECTED", "EXPIRED", "SUPERSEDED",
        name="adminrequeststatus",
        native_enum=False,
    )


def _platform() -> sa.Enum:
    return sa.Enum(
        "WEB", "TELEGRAM", "BALE", "EITAA", "RUBIKA", name="platform", native_enum=False
    )


def _channel_status() -> sa.Enum:
    return sa.Enum(
        "LINKED", "PENDING_VERIFICATION", "UNLINKED",
        name="channellinkstatus",
        native_enum=False,
    )


def _audit_outcome() -> sa.Enum:
    return sa.Enum("SUCCESS", "FAILURE", name="auditoutcome", native_enum=False)


def upgrade() -> None:
    op.create_table(
        "business_types",
        sa.Column("key", sa.String(length=64), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.bulk_insert(
        sa.table(
            "business_types",
            sa.column("key", sa.String()),
            sa.column("name", sa.String()),
            sa.column("description", sa.Text()),
            sa.column("is_active", sa.Boolean()),
        ),
        [
            {
                "key": "general",
                "name": "General business",
                "description": (
                    "Default business type. More types are added as the owner defines them."
                ),
                "is_active": True,
            },
        ],
    )

    op.create_table(
        "users",
        sa.Column("user_id", sa.Uuid(), primary_key=True),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=512), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("account_status", _account_status(), nullable=False),
        sa.Column("failed_login_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_email", "users", ["email"])

    op.create_table(
        "user_sessions",
        sa.Column("session_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("ip", sa.String(length=45), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("token_hash", name="uq_user_sessions_token_hash"),
    )
    op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])

    op.create_table(
        "businesses",
        sa.Column("business_id", sa.Uuid(), primary_key=True),
        sa.Column("business_name", sa.String(length=160), nullable=False),
        sa.Column(
            "business_type_key",
            sa.String(length=64),
            sa.ForeignKey("business_types.key"),
            nullable=False,
        ),
        sa.Column("lifecycle_state", _lifecycle(), nullable=False),
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
    )
    op.create_index("ix_businesses_business_type_key", "businesses", ["business_type_key"])

    op.create_table(
        "memberships",
        sa.Column("membership_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "business_id",
            sa.Uuid(),
            sa.ForeignKey("businesses.business_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", _role(), nullable=False),
        sa.Column("status", _membership_status(), nullable=False),
        sa.Column("permissions", sa.JSON(), nullable=True),
        sa.Column("approved_by", sa.Uuid(), sa.ForeignKey("users.user_id"), nullable=True),
        sa.Column("revoked_by", sa.Uuid(), sa.ForeignKey("users.user_id"), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("user_id", "business_id", name="uq_membership_user_business"),
    )
    op.create_index("ix_memberships_user_id", "memberships", ["user_id"])
    op.create_index("ix_memberships_business_id", "memberships", ["business_id"])
    # Postgres backstop for the single-active-Owner invariant (service-layer
    # check is primary; this is defense-in-depth on Postgres).
    op.create_index(
        "uq_active_owner_per_business",
        "memberships",
        ["business_id"],
        unique=True,
        postgresql_where=sa.text("role = 'OWNER' AND status = 'ACTIVE'"),
    )

    op.create_table(
        "admin_invites",
        sa.Column("invite_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "business_id",
            sa.Uuid(),
            sa.ForeignKey("businesses.business_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("max_uses", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("use_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("code_hash", name="uq_admin_invites_code_hash"),
    )
    op.create_index("ix_admin_invites_business_id", "admin_invites", ["business_id"])

    op.create_table(
        "admin_access_requests",
        sa.Column("request_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "business_id",
            sa.Uuid(),
            sa.ForeignKey("businesses.business_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "candidate_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.user_id"),
            nullable=False,
        ),
        sa.Column("method", _request_method(), nullable=False),
        sa.Column("reference_value", sa.String(length=255), nullable=True),
        sa.Column("status", _request_status(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.Uuid(), sa.ForeignKey("users.user_id"), nullable=True),
    )
    op.create_index(
        "ix_admin_access_requests_business_id", "admin_access_requests", ["business_id"]
    )
    op.create_index(
        "ix_admin_access_requests_candidate_user_id", "admin_access_requests", ["candidate_user_id"]
    )
    op.create_index(
        "uq_pending_admin_request",
        "admin_access_requests",
        ["business_id", "candidate_user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'PENDING'"),
    )

    op.create_table(
        "channel_links",
        sa.Column("link_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "business_id",
            sa.Uuid(),
            sa.ForeignKey("businesses.business_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("platform", _platform(), nullable=False),
        sa.Column("platform_target_id", sa.String(length=128), nullable=False),
        sa.Column("status", _channel_status(), nullable=False),
        sa.Column("control_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "business_id", "platform", "platform_target_id", name="uq_channel_link_target"
        ),
    )
    op.create_index("ix_channel_links_business_id", "channel_links", ["business_id"])

    op.create_table(
        "link_codes",
        sa.Column("code_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("platform", _platform(), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_link_codes_user_id", "link_codes", ["user_id"])
    op.create_index("ix_link_codes_code_hash", "link_codes", ["code_hash"])

    op.create_table(
        "account_identities",
        sa.Column("identity_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("platform", _platform(), nullable=False),
        sa.Column("platform_user_id", sa.String(length=128), nullable=False),
        sa.Column(
            "verified_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("platform", "platform_user_id", name="uq_identity_platform_user"),
        sa.UniqueConstraint("user_id", "platform", name="uq_identity_user_platform"),
    )
    op.create_index("ix_account_identities_user_id", "account_identities", ["user_id"])

    op.create_table(
        "audit_logs",
        sa.Column("log_id", sa.Uuid(), primary_key=True),
        sa.Column("actor_user_id", sa.Uuid(), sa.ForeignKey("users.user_id"), nullable=True),
        sa.Column(
            "business_id", sa.Uuid(), sa.ForeignKey("businesses.business_id"), nullable=True
        ),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("target_type", sa.String(length=40), nullable=True),
        sa.Column("target_id", sa.String(length=64), nullable=True),
        sa.Column("outcome", _audit_outcome(), nullable=False),
        sa.Column("correlation_id", sa.String(length=64), nullable=False),
        sa.Column("meta_data", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_audit_logs_actor_user_id", "audit_logs", ["actor_user_id"])
    op.create_index("ix_audit_logs_business_id", "audit_logs", ["business_id"])
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])
    op.create_index("ix_audit_logs_correlation_id", "audit_logs", ["correlation_id"])


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_table("account_identities")
    op.drop_table("link_codes")
    op.drop_table("channel_links")
    op.drop_table("admin_access_requests")
    op.drop_table("admin_invites")
    op.drop_table("memberships")
    op.drop_table("businesses")
    op.drop_table("user_sessions")
    op.drop_table("users")
    op.drop_table("business_types")
