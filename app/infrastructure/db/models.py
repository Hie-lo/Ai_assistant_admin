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
