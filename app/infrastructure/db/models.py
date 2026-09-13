"""SQLAlchemy ORM models for the Identity/Business/RBAC foundation (Phase 1).

Design notes (failure-first, per the RBAC + Product specs):
- Internal UUIDs are the immutable identities. Email / usernames / display
  names are never used as identity or authorization proof.
- A Membership row is historical: it is transitioned (status/role), not
  deleted, so audit trails survive revocation.
- The single-active-Owner invariant is enforced in the service layer
  (transactional) AND as a Postgres partial unique index (backstop). On
  non-Postgres backends (tests) only the service-layer check applies.
- Session tokens are stored only as SHA-256 hashes; the raw token exists in
  the client cookie only.
- Owner-only permissions can never be persisted into an ADMIN profile
  (see app.domain.permissions.resolve_profile).
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.domain import enums
from app.infrastructure.db.base import Base


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class BusinessType(Base):
    __tablename__ = "business_types"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(
        String(320), nullable=False, unique=True, index=True
    )
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    account_status: Mapped[str] = mapped_column(
        Enum(enums.AccountStatus, native_enum=False, validate_strings=True),
        nullable=False,
        default=enums.AccountStatus.ACTIVE.value,
    )

    # Login rate limiting (bounded, per-user; see app.application.auth).
    failed_login_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Platform-level operator (Phase 2): manages the plan catalog and
    # verifies manual payments. Independent of Business membership/RBAC.
    is_super_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserSession(Base):
    __tablename__ = "user_sessions"

    session_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True
    )
    # SHA-256 hex of the bearer token. The raw token is never persisted.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    user_agent: Mapped[str | None] = mapped_column(String(512))
    ip: Mapped[str | None] = mapped_column(String(45))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Business(Base):
    __tablename__ = "businesses"

    business_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=_uuid)
    business_name: Mapped[str] = mapped_column(String(160), nullable=False)
    business_type_key: Mapped[str] = mapped_column(
        String(64), ForeignKey("business_types.key"), nullable=False, index=True
    )
    lifecycle_state: Mapped[str] = mapped_column(
        Enum(enums.BusinessLifecycle, native_enum=False, validate_strings=True),
        nullable=False,
        default=enums.BusinessLifecycle.ACTIVE.value,
    )
    # Phase 4: automatic AI generation toggle (owner-approved; the trigger
    # itself lands with the sync engine in Phase 8 — this stores intent).
    ai_automatic_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("user_id", "business_id", name="uq_membership_user_business"),
    )
    # NOTE: the single-active-Owner invariant is enforced in the service
    # layer (transactional) and, as a Postgres backstop, by the partial
    # unique index ``uq_active_owner_per_business`` created in the migration.
    # It is intentionally NOT declared in the model metadata because a
    # partial (WHERE) unique index is Postgres-specific; declaring it here
    # would create a full unique index on other backends (e.g. SQLite tests).

    membership_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True
    )
    business_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid,
        ForeignKey("businesses.business_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(
        Enum(enums.MembershipRole, native_enum=False, validate_strings=True), nullable=False
    )
    status: Mapped[str] = mapped_column(
        Enum(enums.MembershipStatus, native_enum=False, validate_strings=True),
        nullable=False,
        default=enums.MembershipStatus.PENDING_REQUEST.value,
    )
    # Granted permission profile (list of permission strings). NULL for OWNER.
    permissions: Mapped[list | None] = mapped_column(JSON)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, ForeignKey("users.user_id")
    )
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, ForeignKey("users.user_id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AdminInvite(Base):
    __tablename__ = "admin_invites"

    invite_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=_uuid)
    business_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid,
        ForeignKey("businesses.business_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_by: Mapped[uuid.UUID] = mapped_column(sa.Uuid, ForeignKey("users.user_id"))
    max_uses: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    use_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AdminAccessRequest(Base):
    __tablename__ = "admin_access_requests"
    # The "at most one PENDING request per (business, candidate)" rule is
    # enforced in the service layer (``_find_or_create_pending``) and, as a
    # Postgres backstop, by the partial unique index
    # ``uq_pending_admin_request`` created in the migration. It is not
    # declared in model metadata for the same Postgres-specific reason noted
    # on Membership above.

    request_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=_uuid)
    business_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid,
        ForeignKey("businesses.business_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    candidate_user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("users.user_id"), nullable=False, index=True
    )
    method: Mapped[str] = mapped_column(
        Enum(enums.AdminRequestMethod, native_enum=False, validate_strings=True), nullable=False
    )
    # Normalized reference (invite handled server-side; channel identifier here).
    reference_value: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(
        Enum(enums.AdminRequestStatus, native_enum=False, validate_strings=True),
        nullable=False,
        default=enums.AdminRequestStatus.PENDING.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, ForeignKey("users.user_id"))


class AccountIdentity(Base):
    __tablename__ = "account_identities"
    __table_args__ = (
        UniqueConstraint("platform", "platform_user_id", name="uq_identity_platform_user"),
        UniqueConstraint("user_id", "platform", name="uq_identity_user_platform"),
    )

    identity_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True
    )
    platform: Mapped[str] = mapped_column(
        Enum(enums.Platform, native_enum=False, validate_strings=True), nullable=False
    )
    platform_user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


class LinkCode(Base):
    """Short-lived one-time code for cross-interface account linking.

    Web shows the code; the user sends it in the Telegram/Bale bot chat; the
    bot verifies it via the internal contract. Codes are single-use and
    expire quickly. Only the SHA-256 digest is stored.
    """

    __tablename__ = "link_codes"

    code_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True
    )
    platform: Mapped[str] = mapped_column(
        Enum(enums.Platform, native_enum=False, validate_strings=True), nullable=False
    )
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ChannelLink(Base):
    """A messaging channel linked to a Business (discovery/verification signal).

    A channel can be used as an admin-request discovery reference ONLY when a
    link exists for the target Business (RBAC spec section 8). Control
    verification (technical proof of control) is completed by the platform
    adapter in a later phase; until then ``control_verified`` stays False.
    """

    __tablename__ = "channel_links"
    __table_args__ = (
        UniqueConstraint(
            "business_id", "platform", "platform_target_id", name="uq_channel_link_target"
        ),
    )

    link_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=_uuid)
    business_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid,
        ForeignKey("businesses.business_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    platform: Mapped[str] = mapped_column(
        Enum(enums.Platform, native_enum=False, validate_strings=True), nullable=False
    )
    platform_target_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(
        Enum(enums.ChannelLinkStatus, native_enum=False, validate_strings=True),
        nullable=False,
        default=enums.ChannelLinkStatus.PENDING_VERIFICATION.value,
    )
    control_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"

    log_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=_uuid)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, ForeignKey("users.user_id"), index=True
    )
    business_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, ForeignKey("businesses.business_id"), index=True
    )
    action: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    target_type: Mapped[str | None] = mapped_column(String(40))
    target_id: Mapped[str | None] = mapped_column(String(64))
    outcome: Mapped[str] = mapped_column(
        Enum(enums.AuditOutcome, native_enum=False, validate_strings=True), nullable=False
    )
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # Named meta_data to avoid clashing with SQLAlchemy's DeclarativeBase.metadata.
    meta_data: Mapped[dict | None] = mapped_column("meta_data", JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )

# --- Phase 2: Subscription / Payment / Entitlement --------------------------


class Plan(Base):
    """Commercial plan catalog entry (operator-managed, no code changes).

    Plans define price + term and the entitlement limits applied to a
    business while its subscription on this plan is live.
    """

    __tablename__ = "plans"

    plan_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String(40), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="IRT")
    price: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    billing_period: Mapped[str] = mapped_column(String(16), nullable=False, default="monthly")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Entitlement limits (None = unlimited).
    product_limit: Mapped[int | None] = mapped_column(Integer)
    source_limit: Mapped[int | None] = mapped_column(Integer)
    channel_limit: Mapped[int | None] = mapped_column(Integer)
    sync_frequency_per_day: Mapped[int | None] = mapped_column(Integer)
    ai_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ai_monthly_credits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Phase 4 (owner-approved 2026-09-13): per-product presets are an
    # exclusive entitlement of the top-tier plan; operator enables it per
    # plan at runtime (no code change), exactly like other limits.
    product_preset_eligible: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    preset_customization: Mapped[str] = mapped_column(String(16), nullable=False, default="none")
    report_level: Mapped[str] = mapped_column(String(16), nullable=False, default="none")
    media_storage_limit_bytes: Mapped[int | None] = mapped_column(Integer)
    admin_seat_limit: Mapped[int | None] = mapped_column(Integer)
    feature_flags: Mapped[dict] = mapped_column("feature_flags", JSON, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )


class Subscription(Base):
    """A business's subscription lifecycle record (one row per purchase cycle).

    Terminal rows (EXPIRED/CANCELLED/REFUNDED) are kept as history; a new
    purchase after a terminal state is a NEW row. At most one non-terminal
    row exists per business (service layer + Postgres partial unique index
    backstop, same pattern as the single-Owner rule).
    """

    __tablename__ = "subscriptions"

    subscription_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=_uuid)
    business_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("businesses.business_id"), nullable=False, index=True
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("plans.plan_id"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        Enum(enums.SubscriptionStatus, native_enum=False, validate_strings=True),
        nullable=False,
        default=enums.SubscriptionStatus.PENDING.value,
    )
    # Plan requested via a pending payment (upgrade/downgrade), applied when
    # that payment is verified.
    pending_plan_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, ForeignKey("plans.plan_id")
    )

    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    grace_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_by: Mapped[uuid.UUID] = mapped_column(sa.Uuid, ForeignKey("users.user_id"))
    updated_by: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, ForeignKey("users.user_id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )


class Payment(Base):
    """Manual-payment record, kept separate from entitlement activation.

    No card/bank secrets are stored: only the operator-entered reference and
    amounts (spec section 5; threat model: no secrets in app tables/logs).
    At most one PENDING payment per subscription (service layer + Postgres
    partial unique index backstop).
    """

    __tablename__ = "payments"

    payment_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=_uuid)
    subscription_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("subscriptions.subscription_id"), nullable=False, index=True
    )
    business_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("businesses.business_id"), nullable=False, index=True
    )
    purpose: Mapped[str] = mapped_column(
        Enum(enums.PaymentPurpose, native_enum=False, validate_strings=True),
        nullable=False,
        default=enums.PaymentPurpose.NEW.value,
    )
    status: Mapped[str] = mapped_column(
        Enum(enums.PaymentStatus, native_enum=False, validate_strings=True),
        nullable=False,
        default=enums.PaymentStatus.PENDING.value,
    )
    expected_amount: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="IRT")
    amount_paid: Mapped[int | None] = mapped_column(Integer)
    reference: Mapped[str | None] = mapped_column(String(120))
    note: Mapped[str | None] = mapped_column(Text)

    initiated_by: Mapped[uuid.UUID] = mapped_column(sa.Uuid, ForeignKey("users.user_id"))
    verified_by: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, ForeignKey("users.user_id"))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


class CreditPool(Base):
    """A business's credit pool (monthly grant or purchased top-up).

    Monthly and purchased pools are separate ledgers (spec section 9);
    ``period_label`` is ``YYYY-MM`` for monthly pools and ``LIFETIME`` for
    the purchased pool.
    """

    __tablename__ = "credit_pools"
    __table_args__ = (
        UniqueConstraint("business_id", "pool_type", "period_label", name="uq_credit_pool"),
    )

    pool_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=_uuid)
    business_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("businesses.business_id"), nullable=False, index=True
    )
    pool_type: Mapped[str] = mapped_column(
        Enum(enums.CreditPoolType, native_enum=False, validate_strings=True), nullable=False
    )
    period_label: Mapped[str] = mapped_column(String(16), nullable=False)
    granted_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    remaining: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )


class CreditTransaction(Base):
    """Append-only credit ledger entry (auditable credit history)."""

    __tablename__ = "credit_transactions"

    tx_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=_uuid)
    pool_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("credit_pools.pool_id"), nullable=False, index=True
    )
    business_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("businesses.business_id"), nullable=False, index=True
    )
    direction: Mapped[str] = mapped_column(
        Enum(enums.CreditDirection, native_enum=False, validate_strings=True), nullable=False
    )
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    # Caller-supplied idempotency key: re-running the same operation returns
    # the original outcome instead of double-consuming.
    idempotency_key: Mapped[str | None] = mapped_column(String(120), unique=True, index=True)
    reference: Mapped[str | None] = mapped_column(String(120), index=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, ForeignKey("users.user_id"))
    meta_data: Mapped[dict | None] = mapped_column("meta_data", JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )

# --- Phase 3: Source / Product ----------------------------------------------


class Source(Base):
    """A connected product source (Excel upload / Google Sheet)."""

    __tablename__ = "sources"

    source_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=_uuid)
    business_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("businesses.business_id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    kind: Mapped[str] = mapped_column(
        Enum(enums.SourceKind, native_enum=False, validate_strings=True), nullable=False
    )
    # External reference: spreadsheet ID for Google Sheets; informational for
    # Excel uploads (no file is persisted in V1 — each import carries the file).
    external_ref: Mapped[str | None] = mapped_column(String(320))
    sheet_name: Mapped[str | None] = mapped_column(String(120))
    range_spec: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(
        Enum(enums.SourceStatus, native_enum=False, validate_strings=True),
        nullable=False,
        default=enums.SourceStatus.PENDING_MAPPING.value,
    )
    # Credentials reference (secret store key) — never the secret itself.
    credentials_ref: Mapped[str | None] = mapped_column(String(120))
    # When False (default), a source refresh must never remove/replace
    # customer-managed media (spec: default safety principle).
    media_authoritative: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_baseline_count: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )


class SourceMapping(Base):
    """Versioned column mapping for a source (spec: mapping is versioned).

    ``entries`` is a JSON list of:
    {"column": str, "canonical_field": str, "field_kind": "CORE"|"CUSTOM",
     "field_type": str, "display_name": str, "required": bool,
     "template_exposed": bool, "confidence": float, "evidence": str}
    Changing a mapping creates a NEW version; old versions are kept.
    """

    __tablename__ = "source_mappings"

    mapping_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=_uuid)
    source_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("sources.source_id"), nullable=False, index=True
    )
    business_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("businesses.business_id"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        Enum(enums.MappingStatus, native_enum=False, validate_strings=True),
        nullable=False,
        default=enums.MappingStatus.DRAFT.value,
    )
    entries: Mapped[list] = mapped_column("entries", JSON, nullable=False, default=list)
    created_by: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, ForeignKey("users.user_id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


class SourceRecord(Base):
    """Current representation of one source item (row) — locator, not identity.

    Row position is never identity; the record links a locator to the
    resolved Product (or nothing when unresolved).
    """

    __tablename__ = "source_records"

    record_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=_uuid)
    source_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("sources.source_id"), nullable=False, index=True
    )
    business_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("businesses.business_id"), nullable=False, index=True
    )
    # Source-specific locator, e.g. "Sheet1!42" or "row:42".
    locator: Mapped[str] = mapped_column(String(220), nullable=False)
    external_key: Mapped[str | None] = mapped_column(String(120), index=True)
    sku: Mapped[str | None] = mapped_column(String(120), index=True)
    barcode: Mapped[str | None] = mapped_column(String(120), index=True)
    fingerprint: Mapped[str | None] = mapped_column(String(64), index=True)
    # SHA-256 of the normalized mapped values (idempotency anchor).
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, ForeignKey("products.product_id"), index=True
    )
    mapping_version_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, ForeignKey("source_mappings.mapping_id")
    )
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="PRESENT")
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )


class Product(Base):
    """Canonical internal product — durable identity and trusted facts.

    ``product_id`` is immutable. Facts are independent of AI content, Posts
    and Publications. Cross-business access is impossible (all queries are
    business-scoped).
    """

    __tablename__ = "products"

    product_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=_uuid)
    business_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("businesses.business_id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(320), nullable=False)
    category: Mapped[str | None] = mapped_column(String(160))
    description: Mapped[str | None] = mapped_column(Text)
    price: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="IRT")
    stock: Mapped[int | None] = mapped_column(Integer)
    external_id: Mapped[str | None] = mapped_column(String(120), index=True)
    sku: Mapped[str | None] = mapped_column(String(120), index=True)
    barcode: Mapped[str | None] = mapped_column(String(120), index=True)
    fingerprint: Mapped[str | None] = mapped_column(String(64), index=True)
    lifecycle_state: Mapped[str] = mapped_column(
        Enum(enums.ProductLifecycle, native_enum=False, validate_strings=True),
        nullable=False,
        default=enums.ProductLifecycle.ACTIVE.value,
    )
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Typed custom attributes (no schema migration per new field).
    attributes: Mapped[dict] = mapped_column("attributes", JSON, nullable=False, default=dict)
    # Compact identity evidence of the last resolution (explainable).
    identity_evidence: Mapped[dict | None] = mapped_column("identity_evidence", JSON)
    # Phase 4: optional per-product preset (owner-approved: completely
    # separate from business-type presets; top-plan entitlement only).
    product_preset_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, ForeignKey("product_presets.product_preset_id"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )


class ProductVersion(Base):
    """Compact version row: change metadata, not a full snapshot."""

    __tablename__ = "product_versions"

    version_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=_uuid)
    product_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("products.product_id"), nullable=False, index=True
    )
    business_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("businesses.business_id"), nullable=False, index=True
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    change_categories: Mapped[list] = mapped_column(
        "change_categories", JSON, nullable=False, default=list
    )
    risk_level: Mapped[str] = mapped_column(
        Enum(enums.ChangeRisk, native_enum=False, validate_strings=True), nullable=False
    )
    changed_fields: Mapped[dict] = mapped_column(
        "changed_fields", JSON, nullable=False, default=dict
    )
    content_hash: Mapped[str | None] = mapped_column(String(64))
    mapping_version_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, ForeignKey("source_mappings.mapping_id")
    )
    trigger: Mapped[str] = mapped_column(
        Enum(enums.SyncTrigger, native_enum=False, validate_strings=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


class ProductMedia(Base):
    """First-class media item (V1: URL references; no server file storage)."""

    __tablename__ = "product_media"

    media_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=_uuid)
    product_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("products.product_id"), nullable=False, index=True
    )
    business_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("businesses.business_id"), nullable=False, index=True
    )
    media_type: Mapped[str] = mapped_column(String(24), nullable=False, default="IMAGE")
    origin: Mapped[str] = mapped_column(
        Enum(enums.MediaOrigin, native_enum=False, validate_strings=True), nullable=False
    )
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fingerprint: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(
        Enum(enums.MediaStatus, native_enum=False, validate_strings=True),
        nullable=False,
        default=enums.MediaStatus.ACTIVE.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )


class ReviewCase(Base):
    """A quarantined, explainable case requiring a human decision."""

    __tablename__ = "review_cases"

    case_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=_uuid)
    business_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("businesses.business_id"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(
        Enum(enums.ReviewCaseKind, native_enum=False, validate_strings=True), nullable=False
    )
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, ForeignKey("products.product_id"), index=True
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, ForeignKey("sources.source_id"), index=True
    )
    # Candidates, evidence, held changes — compact and explainable.
    payload: Mapped[dict] = mapped_column("payload", JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(
        Enum(enums.ReviewCaseStatus, native_enum=False, validate_strings=True),
        nullable=False,
        default=enums.ReviewCaseStatus.OPEN.value,
    )
    resolution: Mapped[str | None] = mapped_column(String(80))
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, ForeignKey("users.user_id"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


class ImportRun(Base):
    """One manual import/sync execution (spec: sync transaction model)."""

    __tablename__ = "import_runs"

    run_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=_uuid)
    source_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("sources.source_id"), nullable=False, index=True
    )
    business_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("businesses.business_id"), nullable=False, index=True
    )
    mapping_version_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, ForeignKey("source_mappings.mapping_id")
    )
    trigger: Mapped[str] = mapped_column(
        Enum(enums.SyncTrigger, native_enum=False, validate_strings=True),
        nullable=False,
        default=enums.SyncTrigger.MANUAL.value,
    )
    status: Mapped[str] = mapped_column(
        Enum(enums.ImportRunStatus, native_enum=False, validate_strings=True),
        nullable=False,
        default=enums.ImportRunStatus.RUNNING.value,
    )
    requested_by: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, ForeignKey("users.user_id"))
    # read/valid/new/changed/unchanged/missing/ambiguous/blocked/error
    counts: Mapped[dict] = mapped_column("counts", JSON, nullable=False, default=dict)
    row_errors: Mapped[list] = mapped_column("row_errors", JSON, nullable=False, default=list)
    failure_summary: Mapped[str | None] = mapped_column(Text)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ---------------------------------------------------------------------------
# Phase 4: Content / Presets / AI output registry
# ---------------------------------------------------------------------------


class Preset(Base):
    """A versioned, platform-neutral content preset for a business type.

    Presets are structured block lists (``preset_versions.blocks``), not
    opaque strings. Each supported business type has a default preset.
    """

    __tablename__ = "presets"

    preset_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=_uuid)
    business_type_key: Mapped[str] = mapped_column(
        String(64), ForeignKey("business_types.key"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, ForeignKey("users.user_id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )
    __table_args__ = (UniqueConstraint("business_type_key", "name", name="uq_presets_type_name"),)


class PresetVersion(Base):
    """Immutable snapshot of one preset's block list."""

    __tablename__ = "preset_versions"

    version_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=_uuid)
    preset_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("presets.preset_id"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    blocks: Mapped[list] = mapped_column("blocks", JSON, nullable=False, default=list)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        Enum(enums.PresetVersionStatus, native_enum=False, validate_strings=True),
        nullable=False,
        default=enums.PresetVersionStatus.DRAFT.value,
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, ForeignKey("users.user_id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    __table_args__ = (UniqueConstraint("preset_id", "version", name="uq_preset_versions_no"),)


class ProductPreset(Base):
    """Per-product preset — a completely separate section from business-type
    presets (owner decision 2026-09-13). Top-plan entitlement only."""

    __tablename__ = "product_presets"

    product_preset_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=_uuid)
    business_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("businesses.business_id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, ForeignKey("users.user_id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )
    __table_args__ = (UniqueConstraint("business_id", "name", name="uq_product_presets_name"),)


class ProductPresetVersion(Base):
    """Immutable snapshot of one per-product preset's block list."""

    __tablename__ = "product_preset_versions"

    version_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=_uuid)
    product_preset_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("product_presets.product_preset_id"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    blocks: Mapped[list] = mapped_column("blocks", JSON, nullable=False, default=list)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        Enum(enums.PresetVersionStatus, native_enum=False, validate_strings=True),
        nullable=False,
        default=enums.PresetVersionStatus.DRAFT.value,
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, ForeignKey("users.user_id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    __table_args__ = (
        UniqueConstraint("product_preset_id", "version", name="uq_ppreset_versions_no"),
    )


class AIOutputDefinition(Base):
    """A configurable, versioned AI output definition (not hard-coded).

    Prompt/policy changes create a NEW version; historical artifacts keep
    referencing the version that produced them (AI spec sections 2-3, 15).
    """

    __tablename__ = "ai_output_definitions"

    definition_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=_uuid)
    key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    prompt_template: Mapped[str] = mapped_column(Text, nullable=False)
    input_fields: Mapped[list] = mapped_column("input_fields", JSON, nullable=False, default=list)
    max_output_length: Mapped[int] = mapped_column(Integer, nullable=False, default=800)
    # provider/model policy, cost policy, retry policy (conceptual fields).
    provider_policy: Mapped[str | None] = mapped_column(String(64))
    cost_credits: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    retry_policy: Mapped[str | None] = mapped_column(String(64))
    allowed_contexts: Mapped[list | None] = mapped_column("allowed_contexts", JSON)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, ForeignKey("users.user_id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    __table_args__ = (UniqueConstraint("key", "version", name="uq_ai_defs_key_version"),)


class AIOutputArtifact(Base):
    """A generated/approved AI output bound to a product + definition version.

    Reuse rule (AI spec section 6): an APPROVED artifact whose source
    dependency fingerprint is unchanged is reused; a prompt/model change
    must never auto-regenerate it.
    """

    __tablename__ = "ai_output_artifacts"

    artifact_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=_uuid)
    product_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("products.product_id"), nullable=False, index=True
    )
    business_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, ForeignKey("businesses.business_id"), nullable=False, index=True
    )
    output_definition_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, ForeignKey("ai_output_definitions.definition_id")
    )
    output_definition_key: Mapped[str] = mapped_column(String(64), nullable=False)
    output_definition_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_dependency_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    generated_value: Mapped[str] = mapped_column(Text, nullable=False)
    approved_value: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        Enum(enums.AIArtifactStatus, native_enum=False, validate_strings=True),
        nullable=False,
        default=enums.AIArtifactStatus.PENDING_APPROVAL.value,
    )
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, ForeignKey("users.user_id"))
    model: Mapped[str | None] = mapped_column(String(120))
    provider: Mapped[str | None] = mapped_column(String(48))
    prompt_chars: Mapped[int | None] = mapped_column(Integer)
    completion_chars: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
