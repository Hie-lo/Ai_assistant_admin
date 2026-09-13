"""Membership / admin-access use cases (RBAC core).

Implements the owner-approved flows:
- Invite codes (primary discovery): owner-generated, expiring, use-limited.
- Channel reference (secondary discovery): only when the channel is already
  linked to the target Business; unknown/ambiguous references fail closed.
- One PENDING request per (business, candidate); duplicates coalesce.
- Approval revalidates ALL prerequisites transactionally (stale-request
  protection) and can never be self-approved.
- Revocation marks the membership REVOKED (history preserved), revokes the
  user's sessions, and immediately blocks future protected actions.

Ownership transfer (changing the OWNER) is intentionally NOT implemented
here: it is a high-risk, separately confirmed workflow (RBAC spec section 6).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application.audit import AuditService
from app.application.authorization import get_active_membership
from app.domain import enums
from app.domain.errors import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.domain.permissions import admin_default_profile
from app.infrastructure.db.models import (
    AdminAccessRequest,
    AdminInvite,
    Business,
    ChannelLink,
    Membership,
    User,
)
from app.infrastructure.security.crypto import (
    generate_invite_code,
    hash_invite_code,
)

INVITE_TTL = timedelta(hours=72)
_INVITE_MAX_USES_CAP = 10


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _as_aware(dt: datetime) -> datetime:
    """Normalize a DB datetime to aware UTC (Postgres aware / SQLite naive)."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _audit(db: Session) -> AuditService:
    return AuditService(db)


def _require_owner(db: Session, *, user: User, business_id: object) -> tuple[Business, Membership]:
    """The actor must be the ACTIVE OWNER of the business (fail closed)."""
    membership = get_active_membership(db, user.user_id, business_id)
    business = db.get(Business, business_id)
    if business is None or membership is None:
        raise AuthorizationError("Not a member of this business")
    if business.lifecycle_state != enums.BusinessLifecycle.ACTIVE.value:
        raise AuthorizationError("Business is not active")
    if membership.role != enums.MembershipRole.OWNER.value:
        raise AuthorizationError("Owner permission required")
    return business, membership


# --- Invites (primary admin-request discovery) ---


def create_invite(
    db: Session, *, owner: User, business_id: object, max_uses: int = 1
) -> tuple[AdminInvite, str]:
    _require_owner(db, user=owner, business_id=business_id)
    if not (1 <= max_uses <= _INVITE_MAX_USES_CAP):
        raise ValidationError("max_uses must be 1-10")

    code = generate_invite_code()
    now = _utcnow()
    invite = AdminInvite(
        business_id=business_id,
        code_hash=hash_invite_code(code),
        created_by=owner.user_id,
        max_uses=max_uses,
        use_count=0,
        is_active=True,
        expires_at=now + INVITE_TTL,
    )
    db.add(invite)
    db.flush()
    _audit(db).record(
        action="admin.invite_created",
        actor_user_id=owner.user_id,
        business_id=business_id,
        target_type="invite",
        target_id=invite.invite_id,
    )
    return invite, code


def _find_valid_invite(db: Session, code: str) -> AdminInvite:
    """Validate an invite code WITHOUT consuming a use.

    Consumption happens only after all business-level checks pass (e.g. the
    candidate is not already a member), so a rejected submission never burns
    an invite use.
    """
    now = _utcnow()
    invite = db.scalar(
        select(AdminInvite).where(AdminInvite.code_hash == hash_invite_code(code.strip()))
    )
    if (
        invite is None
        or not invite.is_active
        or _as_aware(invite.expires_at) <= now
        or invite.use_count >= invite.max_uses
    ):
        _audit(db).record(
            action="admin.invite_invalid", outcome=enums.AuditOutcome.FAILURE
        )
        raise NotFoundError("Invalid or expired invite code")
    return invite


def _consume_invite(db: Session, invite: AdminInvite) -> None:
    """Consume one use of a validated invite (transactional)."""
    invite.use_count += 1
    if invite.use_count >= invite.max_uses:
        invite.is_active = False


# --- Admin access requests ---


def _active_member_exists(db: Session, *, user_id: object, business_id: object) -> bool:
    membership = get_active_membership(db, user_id, business_id)
    return membership is not None


def _find_or_create_pending(
    db: Session,
    *,
    business_id: object,
    candidate: User,
    method: enums.AdminRequestMethod,
    reference_value: str | None,
) -> tuple[AdminAccessRequest, bool]:
    existing = db.scalar(
        select(AdminAccessRequest).where(
            AdminAccessRequest.business_id == business_id,
            AdminAccessRequest.candidate_user_id == candidate.user_id,
            AdminAccessRequest.status == enums.AdminRequestStatus.PENDING.value,
        )
    )
    if existing is not None:
        return existing, False  # coalesced: duplicate submission is a no-op

    request = AdminAccessRequest(
        business_id=business_id,
        candidate_user_id=candidate.user_id,
        method=method.value,
        reference_value=reference_value,
        status=enums.AdminRequestStatus.PENDING.value,
    )
    db.add(request)
    db.flush()
    return request, True


def submit_admin_request(
    db: Session,
    *,
    candidate: User,
    method: str,
    code: str | None = None,
    platform: str | None = None,
    channel_id: str | None = None,
) -> tuple[AdminAccessRequest, bool]:
    """Submit an admin access request. Returns (request, newly_created)."""
    method_enum = {p.value.lower(): p for p in enums.AdminRequestMethod}.get(
        (method or "").strip().lower()
    )
    if method_enum is None:
        raise ValidationError("method must be invite_code or channel_ref")

    invite: AdminInvite | None = None
    if method_enum is enums.AdminRequestMethod.INVITE_CODE:
        if not code or not code.strip():
            raise ValidationError("Invite code is required")
        invite = _find_valid_invite(db, code.strip())
        business_id = invite.business_id
        reference_value: str | None = None
    else:
        platform_key = (platform or "").strip().lower()
        channel_key = (channel_id or "").strip().lower()
        if not platform_key or not channel_key:
            raise ValidationError("platform and channel_id are required")
        platform_enum = {p.value.lower(): p for p in enums.CHANNEL_PLATFORMS}.get(platform_key)
        if platform_enum is None:
            raise ValidationError("Unsupported platform")
        link = db.scalar(
            select(ChannelLink).where(
                ChannelLink.platform == platform_enum.value,
                ChannelLink.platform_target_id == channel_key,
                ChannelLink.status.in_(
                    (
                        enums.ChannelLinkStatus.LINKED.value,
                        enums.ChannelLinkStatus.PENDING_VERIFICATION.value,
                    )
                ),
            )
        )
        if link is None:
            # Fail closed: never reveal which businesses exist.
            raise NotFoundError("No linked business found for this channel")
        business_id = link.business_id
        reference_value = f"{platform_key}:{channel_key}"

    if _active_member_exists(db, user_id=candidate.user_id, business_id=business_id):
        raise ConflictError("You are already an active member of this business")

    # All business checks passed — now consume the invite use (if any).
    if invite is not None:
        _consume_invite(db, invite)

    request, created = _find_or_create_pending(
        db,
        business_id=business_id,
        candidate=candidate,
        method=method_enum,
        reference_value=reference_value,
    )
    _audit(db).record(
        action="admin.request_submitted",
        actor_user_id=candidate.user_id,
        business_id=business_id,
        target_type="admin_request",
        target_id=request.request_id,
        meta={"method": method_enum.value, "created": created},
    )
    return request, created


def list_pending_requests(
    db: Session, *, owner: User, business_id: object
) -> list[AdminAccessRequest]:
    _require_owner(db, user=owner, business_id=business_id)
    rows = db.scalars(
        select(AdminAccessRequest)
        .where(
            AdminAccessRequest.business_id == business_id,
            AdminAccessRequest.status == enums.AdminRequestStatus.PENDING.value,
        )
        .order_by(AdminAccessRequest.created_at.desc())
    ).all()
    return list(rows)


def _get_pending_request(
    db: Session, *, request_id: object, business_id: object
) -> AdminAccessRequest:
    request = db.get(AdminAccessRequest, request_id)
    if request is None or request.business_id != business_id:
        raise NotFoundError("Request not found")
    return request


def approve_request(
    db: Session, *, owner: User, business_id: object, request_id: object
) -> Membership:
    """Approve a pending request: transactional revalidation (RBAC spec 15)."""
    request = _get_pending_request(db, request_id=request_id, business_id=business_id)
    if request.status != enums.AdminRequestStatus.PENDING.value:
        raise ConflictError("Request already resolved")
    business, _owner_membership = _require_owner(db, user=owner, business_id=business_id)
    if owner.user_id == request.candidate_user_id:
        raise AuthorizationError("You cannot approve your own request")
    candidate = db.get(User, request.candidate_user_id)
    if candidate is None or candidate.account_status != enums.AccountStatus.ACTIVE.value:
        raise ConflictError("Candidate account is not active")
    if _active_member_exists(db, user_id=candidate.user_id, business_id=business.business_id):
        raise ConflictError("Candidate is already an active member")

    now = _utcnow()
    profile = sorted(admin_default_profile())
    existing = db.scalar(
        select(Membership).where(
            Membership.user_id == candidate.user_id,
            Membership.business_id == business.business_id,
        )
    )
    if existing is not None:
        # Historical row (e.g. previously revoked) is transitioned, not duplicated.
        existing.role = enums.MembershipRole.ADMIN.value
        existing.status = enums.MembershipStatus.ACTIVE.value
        existing.permissions = profile
        existing.approved_by = owner.user_id
        existing.approved_at = now
        membership = existing
    else:
        membership = Membership(
            user_id=candidate.user_id,
            business_id=business.business_id,
            role=enums.MembershipRole.ADMIN.value,
            status=enums.MembershipStatus.ACTIVE.value,
            permissions=profile,
            approved_by=owner.user_id,
            approved_at=now,
        )
        db.add(membership)
    db.flush()

    request.status = enums.AdminRequestStatus.APPROVED.value
    request.resolved_at = now
    request.resolved_by = owner.user_id
    db.flush()

    _audit(db).record(
        action="admin.request_approved",
        actor_user_id=owner.user_id,
        business_id=business.business_id,
        target_type="admin_request",
        target_id=request.request_id,
        meta={"candidate": str(candidate.user_id)},
    )
    return membership


def reject_request(
    db: Session, *, owner: User, business_id: object, request_id: object
) -> AdminAccessRequest:
    request = _get_pending_request(db, request_id=request_id, business_id=business_id)
    if request.status != enums.AdminRequestStatus.PENDING.value:
        raise ConflictError("Request already resolved")
    business, _owner_membership = _require_owner(db, user=owner, business_id=business_id)
    now = _utcnow()
    request.status = enums.AdminRequestStatus.REJECTED.value
    request.resolved_at = now
    request.resolved_by = owner.user_id
    db.flush()
    _audit(db).record(
        action="admin.request_rejected",
        actor_user_id=owner.user_id,
        business_id=business.business_id,
        target_type="admin_request",
        target_id=request.request_id,
    )
    return request


# --- Revocation ---


def revoke_membership(
    db: Session,
    *,
    owner: User,
    business_id: object,
    membership_id: object,
) -> Membership:
    """Revoke an ADMIN membership (Owner-only). Sessions are revoked too.

    The OWNER membership cannot be revoked here: ownership transfer is a
    separate high-risk workflow.
    """
    business, _owner_membership = _require_owner(db, user=owner, business_id=business_id)
    membership = db.get(Membership, membership_id)
    if membership is None or membership.business_id != business.business_id:
        raise NotFoundError("Membership not found")
    if membership.role == enums.MembershipRole.OWNER.value:
        raise AuthorizationError(
            "Ownership transfer is a separate workflow; revocation of the Owner is not allowed"
        )
    if membership.status != enums.MembershipStatus.ACTIVE.value:
        raise ConflictError("Membership is not active")

    now = _utcnow()
    membership.status = enums.MembershipStatus.REVOKED.value
    membership.revoked_at = now
    membership.revoked_by = owner.user_id
    db.flush()

    from app.application.auth import revoke_all_user_sessions

    revoked_sessions = revoke_all_user_sessions(
        db, membership.user_id, actor_user_id=owner.user_id
    )
    _audit(db).record(
        action="admin.membership_revoked",
        actor_user_id=owner.user_id,
        business_id=business.business_id,
        target_type="membership",
        target_id=membership.membership_id,
        meta={"user": str(membership.user_id), "sessions_revoked": revoked_sessions},
    )
    return membership


def list_members(
    db: Session, *, viewer: User, business_id: object
) -> list[Membership]:
    """Active members of a business (any active member may view)."""
    from app.application.business import require_business_access

    _business, _membership = require_business_access(db, user=viewer, business_id=business_id)
    rows = db.scalars(
        select(Membership)
        .where(
            Membership.business_id == business_id,
            Membership.status == enums.MembershipStatus.ACTIVE.value,
        )
        .order_by(Membership.created_at)
    ).all()
    return list(rows)
