"""Authentication use cases: registration, login, sessions.

Failure-first behavior:
- Locked accounts return a distinct 429 (not a generic failure).
- Unknown email and wrong password both return InvalidCredentials (no user
  enumeration); a dummy hash equalizes verification timing on the unknown
  path.
- Bounded failed attempts (5) trigger a temporary lockout (15 minutes).
- Sessions are server-side, expiring, and revocable; tokens are stored only
  as SHA-256 digests.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.application.audit import AuditService
from app.domain import enums
from app.domain.errors import (
    AccountLocked,
    ConflictError,
    InvalidCredentials,
    NotFoundError,
    ValidationError,
)
from app.infrastructure.db.models import User, UserSession
from app.infrastructure.security.crypto import (
    generate_session_token,
    hash_password,
    hash_token,
    needs_rehash,
    verify_password,
)

PASSWORD_MIN_LENGTH = 10
PASSWORD_MAX_LENGTH = 128
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_DURATION = timedelta(minutes=15)
SESSION_TTL = timedelta(days=30)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_hasher = PasswordHasher()
_dummy_hash_cache: str | None = None


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _as_aware(dt: datetime) -> datetime:
    """Normalize a DB datetime to timezone-aware UTC.

    Postgres returns aware datetimes; SQLite returns naive ones. All
    comparisons in application code must be aware-vs-aware.
    """
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _dummy_hash() -> str:
    """Stable dummy hash so unknown-email logins cost the same as real ones."""
    global _dummy_hash_cache
    if _dummy_hash_cache is None:
        _dummy_hash_cache = _hasher.hash("timing-equalization-dummy")
    return _dummy_hash_cache


def normalize_email(email: str) -> str:
    return email.strip().lower()


def _audit(db: Session) -> AuditService:
    return AuditService(db)


def register_user(db: Session, *, email: str, password: str, display_name: str) -> User:
    """Create a new user account (no business, no role — intent only)."""
    name = display_name.strip()
    if not (1 <= len(name) <= 120):
        raise ValidationError("Display name must be 1-120 characters")
    if not _EMAIL_RE.match(email.strip()):
        raise ValidationError("Invalid email format")
    if len(password) < PASSWORD_MIN_LENGTH:
        raise ValidationError(f"Password must be at least {PASSWORD_MIN_LENGTH} characters")
    if len(password) > PASSWORD_MAX_LENGTH:
        raise ValidationError(f"Password must be at most {PASSWORD_MAX_LENGTH} characters")

    email_norm = normalize_email(email)
    existing = db.scalar(select(User).where(User.email == email_norm))
    if existing is not None:
        raise ConflictError("An account with this email already exists")

    user = User(
        email=email_norm,
        password_hash=hash_password(password),
        display_name=name,
    )
    db.add(user)
    db.flush()
    _audit(db).record(action="auth.register", actor_user_id=user.user_id)
    return user


def authenticate(
    db: Session,
    *,
    email: str,
    password: str,
    ip: str | None = None,
    user_agent: str | None = None,
) -> tuple[User, str]:
    """Verify credentials and issue a new session. Returns (user, token)."""
    email_norm = normalize_email(email)
    user = db.scalar(select(User).where(User.email == email_norm))
    now = _utcnow()

    if user is None:
        verify_password(password, _dummy_hash())  # timing equalization
        raise InvalidCredentials()
    if user.account_status != enums.AccountStatus.ACTIVE.value:
        raise InvalidCredentials()
    if user.locked_until is not None and _as_aware(user.locked_until) > now:
        _audit(db).record(
            action="auth.login_locked",
            outcome=enums.AuditOutcome.FAILURE,
            actor_user_id=user.user_id,
        )
        db.commit()  # persist the audit row even though the request fails
        raise AccountLocked()

    if not verify_password(password, user.password_hash):
        user.failed_login_count += 1
        if user.failed_login_count >= MAX_FAILED_ATTEMPTS:
            user.locked_until = now + LOCKOUT_DURATION
            user.failed_login_count = 0
        db.flush()
        _audit(db).record(
            action="auth.login_failed",
            outcome=enums.AuditOutcome.FAILURE,
            actor_user_id=user.user_id,
        )
        # Failure accounting must survive the failed request: the request
        # dependency rolls back uncommitted work, so commit it explicitly.
        db.commit()
        raise InvalidCredentials()

    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    user.failed_login_count = 0
    user.locked_until = None
    user.last_activity_at = now

    _, token = create_session(db, user, ip=ip, user_agent=user_agent)
    _audit(db).record(action="auth.login", actor_user_id=user.user_id)
    return user, token


def create_session(
    db: Session, user: User, *, ip: str | None = None, user_agent: str | None = None
) -> tuple[UserSession, str]:
    token = generate_session_token()
    now = _utcnow()
    session = UserSession(
        user_id=user.user_id,
        token_hash=hash_token(token),
        user_agent=(user_agent or "")[:512] or None,
        ip=(ip or "")[:45] or None,
        expires_at=now + SESSION_TTL,
    )
    db.add(session)
    db.flush()
    return session, token


def resolve_session(db: Session, token: str | None) -> tuple[User, UserSession] | None:
    """Resolve a bearer token to an active user + session, or None."""
    if not token:
        return None
    row = db.scalar(select(UserSession).where(UserSession.token_hash == hash_token(token)))
    if row is None or row.revoked_at is not None:
        return None
    now = _utcnow()
    if _as_aware(row.expires_at) <= now:
        return None
    user = db.get(User, row.user_id)
    if user is None or user.account_status != enums.AccountStatus.ACTIVE.value:
        return None
    return user, row


def revoke_session(
    db: Session, session: UserSession, *, actor_user_id: object | None = None
) -> None:
    if session.revoked_at is None:
        session.revoked_at = _utcnow()
        db.flush()
    _audit(db).record(
        action="auth.session_revoked",
        actor_user_id=actor_user_id,
        target_type="session",
        target_id=session.session_id,
    )


def revoke_all_user_sessions(
    db: Session, user_id: object, *, actor_user_id: object | None = None
) -> int:
    """Revoke every active session of a user (e.g. admin removal).

    Returns the number of sessions revoked.
    """
    now = _utcnow()
    result = db.execute(
        update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    db.flush()
    count = result.rowcount or 0
    _audit(db).record(
        action="auth.all_sessions_revoked",
        actor_user_id=actor_user_id,
        target_type="user",
        target_id=user_id,
        meta={"count": count},
    )
    return count


def list_user_sessions(db: Session, user_id: object) -> list[UserSession]:
    rows = db.scalars(
        select(UserSession)
        .where(UserSession.user_id == user_id)
        .order_by(UserSession.created_at.desc())
    ).all()
    return list(rows)


def get_user(db: Session, user_id: object) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise NotFoundError("User not found")
    return user
