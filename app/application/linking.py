"""Cross-interface account linking (Web <-> Telegram/Bale) via one-time codes.

Security rules (RBAC spec section 16, threat model section 4):
- Linking requires proof that the same person controls the added identity:
  the code is shown in the authenticated Web session and must be typed into
  the bot chat (which proves control of that chat).
- Never link by matching usernames/names — explicitly prohibited.
- A platform identity already linked to ANOTHER account is a conflict: the
  verify call fails instead of merging accounts.
- Codes are single-use, short-lived, stored only as digests.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application.audit import AuditService
from app.domain import enums
from app.domain.errors import ConflictError, NotFoundError, ValidationError
from app.infrastructure.db.models import AccountIdentity, LinkCode, User
from app.infrastructure.security.crypto import (
    generate_one_time_code,
    hash_code,
)

LINK_CODE_TTL = timedelta(minutes=10)
#: V1 linking targets (Eitaa/Rubika interfaces are integration-blocked).
LINKABLE_PLATFORMS: frozenset[enums.Platform] = frozenset(
    {enums.Platform.TELEGRAM, enums.Platform.BALE}
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _validate_platform(platform: str) -> enums.Platform:
    platform_enum = {p.value.lower(): p for p in LINKABLE_PLATFORMS}.get(
        (platform or "").strip().lower()
    )
    if platform_enum is None:
        raise ValidationError("Unsupported linking platform")
    return platform_enum


def create_link_code(db: Session, *, user: User, platform: str) -> tuple[LinkCode, str]:
    platform_enum = _validate_platform(platform)
    now = _utcnow()
    code = generate_one_time_code()
    row = LinkCode(
        user_id=user.user_id,
        platform=platform_enum.value,
        code_hash=hash_code(code),
        expires_at=now + LINK_CODE_TTL,
    )
    db.add(row)
    db.flush()
    AuditService(db).record(
        action="link.code_created",
        actor_user_id=user.user_id,
        target_type="link_code",
        target_id=row.code_id,
        meta={"platform": platform_enum.value},
    )
    return row, code


def verify_link_code(
    db: Session, *, platform: str, code: str, platform_user_id: str
) -> User:
    """Verify a one-time code against a claimed platform identity.

    Called by the bot interface (internal contract) once the user has typed
    the code into the bot chat. On success the identity is attached to the
    code's owner account.
    """
    platform_enum = _validate_platform(platform)
    platform_identity = (platform_user_id or "").strip()
    if not (1 <= len(platform_identity) <= 128):
        raise ValidationError("Invalid platform identity")

    now = _utcnow()
    row = db.scalar(
        select(LinkCode)
        .where(
            LinkCode.platform == platform_enum.value,
            LinkCode.code_hash == hash_code((code or "").strip()),
            LinkCode.consumed_at.is_(None),
            LinkCode.expires_at > now,
        )
        .order_by(LinkCode.created_at.desc())
    )
    if row is None:
        AuditService(db).record(
            action="link.code_verify_failed",
            outcome=enums.AuditOutcome.FAILURE,
            meta={"platform": platform_enum.value, "reason": "invalid_or_expired"},
        )
        raise NotFoundError("Invalid or expired link code")

    user = db.get(User, row.user_id)
    if user is None or user.account_status != enums.AccountStatus.ACTIVE.value:
        raise ConflictError("Account is not active")

    existing_identity = db.scalar(
        select(AccountIdentity).where(
            AccountIdentity.platform == platform_enum.value,
            AccountIdentity.platform_user_id == platform_identity,
        )
    )
    if existing_identity is not None:
        if existing_identity.user_id != user.user_id:
            # Never merge accounts on a name/id collision (threat model 4).
            AuditService(db).record(
                action="link.identity_conflict",
                outcome=enums.AuditOutcome.FAILURE,
                meta={"platform": platform_enum.value},
            )
            raise ConflictError(
                "This platform identity is already linked to another account"
            )
        # Already linked to this account: idempotent success.
        row.consumed_at = now
        db.flush()
        AuditService(db).record(
            action="link.identity_already_linked",
            actor_user_id=user.user_id,
            meta={"platform": platform_enum.value},
        )
        return user

    row.consumed_at = now
    db.flush()
    identity = AccountIdentity(
        user_id=user.user_id,
        platform=platform_enum.value,
        platform_user_id=platform_identity,
    )
    db.add(identity)
    db.flush()
    AuditService(db).record(
        action="link.identity_linked",
        actor_user_id=user.user_id,
        target_type="account_identity",
        target_id=identity.identity_id,
        meta={"platform": platform_enum.value},
    )
    return user


def list_identities(db: Session, *, user: User) -> list[AccountIdentity]:
    rows = db.scalars(
        select(AccountIdentity).where(AccountIdentity.user_id == user.user_id)
    ).all()
    return list(rows)
