"""Platform connection service (Phase 5, Telegram V1).

Owner decision 2026-09-15: ONE shared organization bot managed by the
platform operator (credential from the environment). A business connects a
target channel/group it has added the bot to as admin.

Connection verification proves technical control (adapter spec section 11):
1. the shared bot works (getMe);
2. the target chat exists (getChat);
3. the bot is an administrator of that chat (getChatMember).
Verification is re-runnable after reconnect and detects permission loss.
No secret is stored per business; nothing sensitive is logged (rule 14).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application.audit import AuditService
from app.domain import enums
from app.domain.errors import ConflictError, NotFoundError, ValidationError
from app.infrastructure.db.models import (
    Business,
    PlatformConnection,
    Publication,
    User,
)
from app.infrastructure.platforms import telegram as tg

#: Bot chat-member statuses that prove control.
_ADMIN_STATUSES = frozenset({"creator", "administrator"})


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _target_key(target: str) -> str:
    target = (target or "").strip()
    if not target:
        raise ValidationError("a connection target (channel username or chat id) is required")
    if target.startswith("@"):
        target = target[1:]
    if not 3 <= len(target) <= 128:
        raise ValidationError("target must be 3-128 chars (username or numeric chat id)")
    return target


def _client() -> tg.TelegramClient:
    return tg.get_telegram_client()


def _get_connection(
    db: Session, *, business: Business, connection_id: uuid.UUID
) -> PlatformConnection:
    conn = db.get(PlatformConnection, connection_id)
    if conn is None or conn.business_id != business.business_id:
        raise NotFoundError("connection not found")
    return conn


def list_connections(db: Session, *, business: Business) -> list[PlatformConnection]:
    return list(
        db.scalars(
            select(PlatformConnection)
            .where(PlatformConnection.business_id == business.business_id)
            .order_by(PlatformConnection.created_at)
        ).all()
    )


def _verify_with_client(
    db: Session, conn: PlatformConnection, client: tg.TelegramClient
) -> PlatformConnection:
    """Run the three-step verification; moves the connection status.

    Returns the updated connection. Raises ValidationError when the shared
    bot itself is unavailable (a platform-level configuration problem).
    """
    try:
        me = client.get_me()
    except tg.TelegramError as exc:
        if exc.code in (
            enums.PublicationErrorCode.AUTHENTICATION_ERROR,
            enums.PublicationErrorCode.NETWORK_TIMEOUT,
            enums.PublicationErrorCode.NETWORK_ERROR,
        ):
            raise ValidationError(
                "the shared Telegram bot is not available "
                f"({exc.code.value}); check the platform configuration"
            ) from exc
        conn.status = enums.PlatformConnectionStatus.PERMISSION_LOST.value
        conn.last_error_code = exc.code.value
        db.flush()
        return conn
    bot_id = int(me.get("id") or 0)
    if not bot_id:
        raise ValidationError("the shared Telegram bot did not report an identity")

    try:
        chat = client.get_chat(conn.platform_target_id)
    except tg.TelegramError as exc:
        conn.status = enums.PlatformConnectionStatus.PERMISSION_LOST.value
        conn.last_error_code = exc.code.value
        db.flush()
        return conn
    chat_id = str(chat.get("id") or conn.platform_target_id)

    # Two different target names can resolve to the SAME chat id. Fail
    # closed with a clean conflict instead of tripping the unique index.
    conflict = db.scalars(
        select(PlatformConnection).where(
            PlatformConnection.business_id == conn.business_id,
            PlatformConnection.platform == conn.platform,
            PlatformConnection.connection_id != conn.connection_id,
            PlatformConnection.platform_target_id == chat_id,
        )
    ).first()
    if conflict is not None:
        raise ConflictError(
            f"this chat is already connected (as '{conflict.target_name}')"
        )

    try:
        member = client.get_chat_member(chat_id, bot_id)
    except tg.TelegramError as exc:
        conn.status = enums.PlatformConnectionStatus.PERMISSION_LOST.value
        conn.last_error_code = exc.code.value
        db.flush()
        return conn

    if member.get("status") in _ADMIN_STATUSES:
        conn.status = enums.PlatformConnectionStatus.VERIFIED.value
        conn.control_verified = True
        conn.last_verified_at = _utcnow()
        conn.last_error_code = None
        conn.platform_target_id = chat_id  # normalize to the canonical chat id
        # Resume publications that were suspended by a lost permission.
        _resume_suspended(db, conn)
    else:
        conn.status = enums.PlatformConnectionStatus.PERMISSION_LOST.value
        conn.control_verified = False
        conn.last_error_code = enums.PublicationErrorCode.PERMISSION_ERROR.value
        # Live publications lose control immediately.
        _suspend_live(db, conn, status=enums.PlatformConnectionStatus.PERMISSION_LOST.value)
    db.flush()
    return conn


def _suspend_live(db: Session, conn: PlatformConnection, *, status: str) -> None:
    """Suspend every publication that holds (or may hold) a remote message.

    Sources are all states except the terminal/dead ones — live, unresolved
    (retryable/unknown) and, only after a mid-request crash, in-flight.
    DISCONNECTED / PERMISSION_LOST rows are already suspended.
    """
    from app.domain.publication import assert_transition

    dead_ends = {
        enums.PublicationStatus.NOT_PUBLISHED.value,
        enums.PublicationStatus.REMOTE_DELETED.value,
        enums.PublicationStatus.FAILED_FINAL.value,
        enums.PublicationStatus.DISCONNECTED.value,
        enums.PublicationStatus.PERMISSION_LOST.value,
    }
    suspendable = [
        s.value for s in enums.PublicationStatus if s.value not in dead_ends
    ]
    target = (
        enums.PublicationStatus.PERMISSION_LOST.value
        if status == enums.PlatformConnectionStatus.PERMISSION_LOST.value
        else enums.PublicationStatus.DISCONNECTED.value
    )
    for pub in db.scalars(
        select(Publication).where(
            Publication.connection_id == conn.connection_id,
            Publication.status.in_(suspendable),
        )
    ).all():
        try:
            assert_transition(
                enums.PublicationStatus(pub.status), enums.PublicationStatus(target)
            )
            pub.status = target
            pub.last_attempt_at = _utcnow()
        except ValueError:
            # Fail closed: an unmodelled source stays as-is; the
            # reconciliation after (re)verification is the recovery path.
            continue



def _resume_suspended(db: Session, conn: PlatformConnection) -> None:
    """After a successful (re)verification, reconcile suspended
    publications against the remote — resume only what still exists;
    never bulk-republish (adapter spec section 15)."""
    from app.application import publications as pubsvc
    from app.domain import enums as e

    for pub in db.scalars(
        select(Publication).where(
            Publication.connection_id == conn.connection_id,
            Publication.status.in_(
                [
                    e.PublicationStatus.PERMISSION_LOST.value,
                    e.PublicationStatus.DISCONNECTED.value,
                ]
            ),
        )
    ).all():
        pubsvc._reconcile_remote(db, pub, client=_client())


def create_connection(
    db: Session,
    *,
    business: Business,
    actor: User,
    platform: str,
    target: str,
) -> PlatformConnection:
    if platform != enums.Platform.TELEGRAM.value:
        raise ValidationError("only the TELEGRAM platform is available in V1")
    target = _target_key(target)
    existing = db.scalars(
        select(PlatformConnection).where(
            PlatformConnection.business_id == business.business_id,
            PlatformConnection.platform == platform,
            PlatformConnection.platform_target_id == target,
        )
    ).first()
    if existing is not None:
        if existing.status == enums.PlatformConnectionStatus.DISCONNECTED.value:
            raise ConflictError("this target was disconnected; reconnect it instead")
        raise ConflictError("this target is already connected")
    conn = PlatformConnection(
        business_id=business.business_id,
        platform=platform,
        target_name=target,
        platform_target_id=target,
        status=enums.PlatformConnectionStatus.PENDING_VERIFICATION.value,
        created_by=actor.user_id,
    )
    db.add(conn)
    db.flush()
    _verify_with_client(db, conn, _client())
    AuditService(db).record(
        action="connection.created",
        actor_user_id=actor.user_id,
        business_id=business.business_id,
        target_type="connection",
        target_id=str(conn.connection_id),
        meta={"platform": platform, "status": conn.status},
    )
    return conn


def verify_connection(
    db: Session, *, business: Business, actor: User, connection_id: uuid.UUID
) -> PlatformConnection:
    conn = _get_connection(db, business=business, connection_id=connection_id)
    if conn.status == enums.PlatformConnectionStatus.DISCONNECTED.value:
        raise ConflictError("a disconnected connection cannot be re-verified; reconnect it")
    _verify_with_client(db, conn, _client())
    AuditService(db).record(
        action="connection.verified",
        actor_user_id=actor.user_id,
        business_id=business.business_id,
        target_type="connection",
        target_id=str(conn.connection_id),
        meta={"status": conn.status},
    )
    return conn


def reconnect_connection(
    db: Session, *, business: Business, actor: User, connection_id: uuid.UUID
) -> PlatformConnection:
    """Reconnect a disconnected connection (re-runs verification)."""
    conn = _get_connection(db, business=business, connection_id=connection_id)
    if conn.status != enums.PlatformConnectionStatus.DISCONNECTED.value:
        raise ConflictError("only a disconnected connection can be reconnected")
    conn.status = enums.PlatformConnectionStatus.PENDING_VERIFICATION.value
    db.flush()
    _verify_with_client(db, conn, _client())
    AuditService(db).record(
        action="connection.reconnected",
        actor_user_id=actor.user_id,
        business_id=business.business_id,
        target_type="connection",
        target_id=str(conn.connection_id),
        meta={"status": conn.status},
    )
    return conn


def disconnect_connection(
    db: Session, *, business: Business, actor: User, connection_id: uuid.UUID
) -> PlatformConnection:
    conn = _get_connection(db, business=business, connection_id=connection_id)
    if conn.status == enums.PlatformConnectionStatus.DISCONNECTED.value:
        raise ConflictError("the connection is already disconnected")
    _suspend_live(
        db, conn, status=enums.PlatformConnectionStatus.DISCONNECTED.value
    )
    conn.status = enums.PlatformConnectionStatus.DISCONNECTED.value
    conn.control_verified = False
    db.flush()
    AuditService(db).record(
        action="connection.disconnected",
        actor_user_id=actor.user_id,
        business_id=business.business_id,
        target_type="connection",
        target_id=str(conn.connection_id),
    )
    return conn
