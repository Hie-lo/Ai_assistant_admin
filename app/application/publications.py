"""Publication service (Phase 5 application layer).

Manual publish/update/repost/delete/reconcile for Telegram V1
(owner-approved 2026-09-15). Publication runs SYNCHRONOUSLY in the request
(queue workers are Phase 8); the state machine, idempotency and attempt
records are identical to what the workers will drive later.

Failure-first rules implemented (developer directive + spec sections
13-21, 25):
- a timeout is NEVER classified as failed: UNKNOWN_REMOTE_STATE, then
  reconcile;
- a repost is a NEW publication: publish new -> verify -> delete old;
- a remote manual deletion is recorded, never auto-reposted;
- one publication's failure never affects another (per-row state).
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application import content_preview
from app.application import entitlements as entsvc
from app.application.audit import AuditService
from app.domain import enums
from app.domain import publication as pubdomain
from app.domain.errors import ConflictError, NotFoundError, ValidationError
from app.infrastructure.db.models import (
    Business,
    PlatformConnection,
    Post,
    PostVersion,
    Product,
    Publication,
    PublicationAttempt,
    User,
)
from app.infrastructure.platforms import telegram as tg

#: Statuses that occupy the (connection, product) slot: everything that is
#: not a dead end. A live publication blocks a NEW publish (effectively-
#: once, spec section 14) until it resolves to a terminal state.
_NON_LIVE_STATUSES = frozenset(
    {
        enums.PublicationStatus.NOT_PUBLISHED.value,
        enums.PublicationStatus.FAILED_FINAL.value,
        enums.PublicationStatus.REMOTE_DELETED.value,
    }
)
LIVE_STATUSES = frozenset(
    s.value for s in enums.PublicationStatus if s.value not in _NON_LIVE_STATUSES
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _fp(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _media_fp(urls: list[str]) -> str:
    return hashlib.sha256("|".join(urls or []).encode("utf-8")).hexdigest()


def _get_product(db: Session, *, business: Business, product_id: uuid.UUID) -> Product:
    product = db.get(Product, product_id)
    if product is None or product.business_id != business.business_id:
        raise NotFoundError("product not found")
    return product


def _get_connection(
    db: Session, *, business: Business, connection_id: uuid.UUID
) -> PlatformConnection:
    conn = db.get(PlatformConnection, connection_id)
    if conn is None or conn.business_id != business.business_id:
        raise NotFoundError("connection not found")
    return conn


def _get_publication(
    db: Session, *, business: Business, publication_id: uuid.UUID
) -> Publication:
    pub = db.get(Publication, publication_id)
    if pub is None or pub.business_id != business.business_id:
        raise NotFoundError("publication not found")
    return pub


def _require_verified(
    db: Session, *, business: Business, connection_id: uuid.UUID
) -> PlatformConnection:
    conn = _get_connection(db, business=business, connection_id=connection_id)
    if conn.platform != enums.Platform.TELEGRAM.value:
        raise ValidationError("unsupported platform")
    if conn.status != enums.PlatformConnectionStatus.VERIFIED.value:
        raise ConflictError("the connection must be verified before publishing")
    return conn


def _require_publishable_product(db: Session, product: Product) -> None:
    if product.lifecycle_state == enums.ProductLifecycle.REVIEW_REQUIRED.value:
        raise ConflictError(
            "the product is under identity review; publication changes are "
            "blocked until the review is resolved"
        )
    if product.lifecycle_state == enums.ProductLifecycle.ARCHIVED.value:
        raise ConflictError("archived products cannot be published")


def live_publication(
    db: Session, *, connection_id: uuid.UUID, product_id: uuid.UUID
) -> Publication | None:
    rows = db.scalars(
        select(Publication).where(
            Publication.connection_id == connection_id,
            Publication.product_id == product_id,
            Publication.status.in_(list(LIVE_STATUSES)),
        )
    ).all()
    return rows[0] if rows else None


def _attempt(
    db: Session,
    *,
    publication: Publication,
    operation: enums.PublicationOperation,
    status: enums.AttemptStatus,
    error_code: str | None = None,
    error_detail: str | None = None,
    remote_message_id: str | None = None,
) -> PublicationAttempt:
    row = PublicationAttempt(
        publication_id=publication.publication_id,
        operation=operation,
        status=status,
        error_code=error_code,
        error_detail=(error_detail or "")[:300] or None,
        remote_message_id=remote_message_id,
        started_at=_utcnow(),
        finished_at=_utcnow(),
    )
    db.add(row)
    db.flush()
    return row


def _transition(pub: Publication, target: enums.PublicationStatus) -> None:
    pubdomain.assert_transition(enums.PublicationStatus(pub.status), target)
    pub.status = target.value
    pub.last_attempt_at = _utcnow()


def _render_for_platform(
    db: Session, *, business: Business, product_id: uuid.UUID, caps: tg.TelegramCapabilities
) -> tuple[str, list[str]]:
    """Render through the Phase 4 engine under the platform's hard limits.

    Without media the text is plain text (<=4096). With media the text is
    the CAPTION (<=1024): when the full text would overflow, the renderer
    re-renders with the caption limit — trimming by approved priority and
    BLOCKING (never silently truncating) when the essentials don't fit.
    Media is capped at the platform's album size (first N eligible).
    """
    preview = content_preview.preview_product(
        db, business=business, product_id=product_id
    )
    media = preview["media_urls"][: caps.media_group_max]
    if preview["blocked_reason"]:
        raise ValidationError(preview["blocked_reason"])
    text = preview["text"]
    if media and len(text) > caps.caption_max_length:
        preview = content_preview.preview_product(
            db,
            business=business,
            product_id=product_id,
            max_length=caps.caption_max_length,
        )
        if preview["blocked_reason"]:
            raise ValidationError(preview["blocked_reason"])
        text = preview["text"]
    return text, media


def _post_version(
    db: Session,
    *,
    business: Business,
    product: Product,
    text: str,
    media: list[str],
) -> PostVersion:
    post = db.scalars(
        select(Post).where(
            Post.business_id == business.business_id,
            Post.product_id == product.product_id,
        )
    ).first()
    if post is None:
        post = Post(
            business_id=business.business_id,
            product_id=product.product_id,
            status=enums.PostStatus.PUBLISHED.value,
        )
        db.add(post)
        db.flush()
    versions = db.scalars(
        select(PostVersion).where(PostVersion.post_id == post.post_id)
    ).all()
    next_no = (max((v.version for v in versions), default=0)) + 1
    version = PostVersion(
        post_id=post.post_id,
        version=next_no,
        content_fingerprint=_fp(text),
        media_fingerprint=_media_fp(media),
        text=text,
        media_urls=list(media),
    )
    db.add(version)
    db.flush()
    return version


def _find_by_idempotency_key(
    db: Session, *, idempotency_key: str
) -> Publication | None:
    return db.scalars(
        select(Publication).where(Publication.idempotency_key == idempotency_key)
    ).first()


def publish(
    db: Session,
    *,
    business: Business,
    actor: User,
    product_id: uuid.UUID,
    connection_id: uuid.UUID,
) -> dict:
    """Publish a product to a verified connection (manual, V1)."""
    product = _get_product(db, business=business, product_id=product_id)
    entsvc.require_entitlement_unchecked(db, business_id=business.business_id)
    conn = _require_verified(db, business=business, connection_id=connection_id)
    _require_publishable_product(db, product)

    existing = live_publication(
        db, connection_id=conn.connection_id, product_id=product.product_id
    )
    if existing is not None:
        return {"publication": existing, "created": False}

    caps = tg.TELEGRAM_CAPABILITIES
    text, media = _render_for_platform(
        db, business=business, product_id=product.product_id, caps=caps
    )
    version = _post_version(
        db, business=business, product=product, text=text, media=media
    )
    idem = pubdomain.publish_idempotency_key(
        business_id=str(business.business_id),
        product_id=str(product.product_id),
        connection_id=str(conn.connection_id),
        post_version_id=str(version.version_id),
    )
    existing = _find_by_idempotency_key(db, idempotency_key=idem)
    if existing is not None:
        return {"publication": existing, "created": False}

    pub = Publication(
        post_id=version.post_id,
        post_version_id=version.version_id,
        connection_id=conn.connection_id,
        business_id=business.business_id,
        product_id=product.product_id,
        status=enums.PublicationStatus.NOT_PUBLISHED.value,
        idempotency_key=idem,
    )
    db.add(pub)
    db.flush()
    _transition(pub, enums.PublicationStatus.QUEUED)
    _transition(pub, enums.PublicationStatus.PUBLISHING)

    client = tg.get_telegram_client()
    chat_id = conn.platform_target_id
    try:
        if media:
            ids = client.send_media_group(chat_id, media, caption=text)
            remote_id = str(ids[0]) if ids else None
            if not remote_id:
                raise tg.TelegramError(
                    enums.PublicationErrorCode.REMOTE_UNKNOWN,
                    "media group accepted without a message id",
                )
        else:
            remote_id = str(client.send_message(chat_id, text))
    except tg.TelegramError as exc:
        if exc.code is enums.PublicationErrorCode.NETWORK_TIMEOUT:
            _transition(pub, enums.PublicationStatus.UNKNOWN_REMOTE_STATE)
            pub.error_code = exc.code.value
            _attempt(
                db,
                publication=pub,
                operation=enums.PublicationOperation.PUBLISH,
                status=enums.AttemptStatus.UNKNOWN,
                error_code=exc.code.value,
                error_detail=exc.detail,
            )
        elif pubdomain.is_retryable(exc.code.value):
            _transition(pub, enums.PublicationStatus.FAILED_RETRYABLE)
            pub.error_code = exc.code.value
            _attempt(
                db,
                publication=pub,
                operation=enums.PublicationOperation.PUBLISH,
                status=enums.AttemptStatus.FAILED,
                error_code=exc.code.value,
                error_detail=exc.detail,
            )
        else:
            _transition(pub, enums.PublicationStatus.FAILED_FINAL)
            pub.error_code = exc.code.value
            _attempt(
                db,
                publication=pub,
                operation=enums.PublicationOperation.PUBLISH,
                status=enums.AttemptStatus.FAILED,
                error_code=exc.code.value,
                error_detail=exc.detail,
            )
        AuditService(db).record(
            action="publication.failed",
            outcome=enums.AuditOutcome.FAILURE,
            actor_user_id=actor.user_id,
            business_id=business.business_id,
            target_type="publication",
            target_id=str(pub.publication_id),
            meta={"error_code": exc.code.value, "status": pub.status},
        )
        db.flush()
        return {"publication": pub, "created": True}

    pub.remote_message_id = remote_id
    pub.remote_fingerprint = _fp(text)
    _transition(pub, enums.PublicationStatus.PUBLISHED)
    _attempt(
        db,
        publication=pub,
        operation=enums.PublicationOperation.PUBLISH,
        status=enums.AttemptStatus.SUCCESS,
        remote_message_id=remote_id,
    )
    AuditService(db).record(
        action="publication.published",
        actor_user_id=actor.user_id,
        business_id=business.business_id,
        target_type="publication",
        target_id=str(pub.publication_id),
        meta={
            "product": str(product.product_id),
            "connection": str(conn.connection_id),
            "media": len(media),
        },
    )
    db.flush()
    return {"publication": pub, "created": True}


def _render_snapshot(db: Session, *, business: Business, product_id: uuid.UUID):
    caps = tg.TELEGRAM_CAPABILITIES
    text, media = _render_for_platform(db, business=business, product_id=product_id, caps=caps)
    return text, media


def update_publication(
    db: Session, *, business: Business, actor: User, publication_id: uuid.UUID
) -> dict:
    """Apply the product's current content to a PUBLISHED publication.

    Automatic classification (owner-approved): NOOP / EDIT / REPOST.
    """
    pub = _get_publication(db, business=business, publication_id=publication_id)
    if pub.status != enums.PublicationStatus.PUBLISHED.value:
        raise ConflictError(
            f"only a PUBLISHED publication can be updated (status: {pub.status})"
        )
    product = _get_product(db, business=business, product_id=pub.product_id)
    _require_publishable_product(db, product)
    conn = _get_connection(
        db, business=business, connection_id=pub.connection_id
    )
    if conn.status != enums.PlatformConnectionStatus.VERIFIED.value:
        raise ConflictError("the connection must be verified before updates")

    old_version = db.get(PostVersion, pub.post_version_id)
    old_snap = pubdomain.ContentSnapshot(
        content_fingerprint=old_version.content_fingerprint,
        media_fingerprint=old_version.media_fingerprint,
    )
    caps = tg.TELEGRAM_CAPABILITIES
    text, media = _render_for_platform(
        db, business=business, product_id=product.product_id, caps=caps
    )
    new_snap = pubdomain.ContentSnapshot(
        content_fingerprint=_fp(text), media_fingerprint=_media_fp(media)
    )
    plan = pubdomain.plan_update(
        old_snap, new_snap, edit_supported=caps.edit_text and caps.edit_caption
    )
    if plan is pubdomain.UpdatePlan.NOOP:
        return {"publication": pub, "plan": plan.value, "updated": False}

    if plan is pubdomain.UpdatePlan.EDIT:
        return _execute_edit(
            db, business=business, actor=actor, pub=pub, text=text, media=media
        )
    return _execute_repost(
        db, business=business, actor=actor, pub=pub, text=text, media=media
    )


def _execute_edit(
    db: Session,
    *,
    business: Business,
    actor: User,
    pub: Publication,
    text: str,
    media: list[str],
) -> dict:
    product = db.get(Product, pub.product_id)
    _transition(pub, enums.PublicationStatus.UPDATE_PENDING)
    _transition(pub, enums.PublicationStatus.UPDATING)
    version = _post_version(
        db, business=business, product=product, text=text, media=media
    )
    conn = db.get(PlatformConnection, pub.connection_id)
    client = tg.get_telegram_client()
    try:
        if media:
            client.edit_message_caption(conn.platform_target_id, int(pub.remote_message_id), text)
        else:
            client.edit_message_text(conn.platform_target_id, int(pub.remote_message_id), text)
    except tg.TelegramError as exc:
        _apply_remote_failure(db, pub, exc, operation=enums.PublicationOperation.EDIT)
        return {"publication": pub, "plan": pubdomain.UpdatePlan.EDIT.value, "updated": False}

    pub.post_version_id = version.version_id
    pub.remote_fingerprint = _fp(text)
    _transition(pub, enums.PublicationStatus.PUBLISHED)
    _attempt(
        db,
        publication=pub,
        operation=enums.PublicationOperation.EDIT,
        status=enums.AttemptStatus.SUCCESS,
    )
    AuditService(db).record(
        action="publication.edited",
        actor_user_id=actor.user_id,
        business_id=business.business_id,
        target_type="publication",
        target_id=str(pub.publication_id),
        meta={"product": str(pub.product_id)},
    )
    db.flush()
    return {"publication": pub, "plan": pubdomain.UpdatePlan.EDIT.value, "updated": True}


def _apply_remote_failure(
    db: Session, pub: Publication, exc: tg.TelegramError, *, operation
) -> None:
    if exc.code is enums.PublicationErrorCode.NETWORK_TIMEOUT:
        target = enums.PublicationStatus.UNKNOWN_REMOTE_STATE
        attempt_status = enums.AttemptStatus.UNKNOWN
    elif pubdomain.is_retryable(exc.code.value):
        target = enums.PublicationStatus.FAILED_RETRYABLE
        attempt_status = enums.AttemptStatus.FAILED
    else:
        target = enums.PublicationStatus.FAILED_FINAL
        attempt_status = enums.AttemptStatus.FAILED
    _transition(pub, target)
    pub.error_code = exc.code.value
    _attempt(
        db,
        publication=pub,
        operation=operation,
        status=attempt_status,
        error_code=exc.code.value,
        error_detail=exc.detail,
    )


def _delete_old_remote(
    db: Session, *, pub: Publication, conn: PlatformConnection, client: tg.TelegramClient
) -> None:
    """Delete the old remote message of `pub` (already PUBLISHED).

    NOT_FOUND is a success (the message is already gone). Other failures
    leave a TRACKED state (FAILED_RETRYABLE / UNKNOWN_REMOTE_STATE) — the
    lingering remote message is never silently lost (spec section 19).
    """
    _transition(pub, enums.PublicationStatus.DELETE_PENDING)
    _transition(pub, enums.PublicationStatus.DELETING)
    try:
        client.delete_message(conn.platform_target_id, int(pub.remote_message_id))
    except tg.TelegramError as exc:
        if exc.code is enums.PublicationErrorCode.NOT_FOUND:
            pub.remote_message_id = None
            _transition(pub, enums.PublicationStatus.REMOTE_DELETED)
            _attempt(
                db,
                publication=pub,
                operation=enums.PublicationOperation.DELETE,
                status=enums.AttemptStatus.SUCCESS,
            )
            return
        # The replacement is (or will become) live; the old one may linger
        # (tracked, retryable). Content is never lost.
        _apply_remote_failure(db, pub, exc, operation=enums.PublicationOperation.DELETE)
        return
    pub.remote_message_id = None
    _transition(pub, enums.PublicationStatus.REMOTE_DELETED)
    _attempt(
        db,
        publication=pub,
        operation=enums.PublicationOperation.DELETE,
        status=enums.AttemptStatus.SUCCESS,
    )


def _execute_repost(
    db: Session,
    *,
    business: Business,
    actor: User,
    pub: Publication,
    text: str,
    media: list[str],
) -> dict:
    """Owner-approved safe order: publish NEW -> verify -> delete OLD."""
    product = db.get(Product, pub.product_id)
    conn = db.get(PlatformConnection, pub.connection_id)
    version = _post_version(
        db, business=business, product=product, text=text, media=media
    )
    idem = pubdomain.publish_idempotency_key(
        business_id=str(business.business_id),
        product_id=str(product.product_id),
        connection_id=str(conn.connection_id),
        post_version_id=str(version.version_id),
    )
    new_pub = Publication(
        post_id=version.post_id,
        post_version_id=version.version_id,
        connection_id=conn.connection_id,
        business_id=business.business_id,
        product_id=product.product_id,
        status=enums.PublicationStatus.NOT_PUBLISHED.value,
        idempotency_key=idem,
    )
    db.add(new_pub)
    db.flush()
    _transition(new_pub, enums.PublicationStatus.QUEUED)
    _transition(new_pub, enums.PublicationStatus.PUBLISHING)

    client = tg.get_telegram_client()
    try:
        if media:
            ids = client.send_media_group(conn.platform_target_id, media, caption=text)
            remote_id = str(ids[0]) if ids else None
            if not remote_id:
                raise tg.TelegramError(
                    enums.PublicationErrorCode.REMOTE_UNKNOWN,
                    "media group accepted without a message id",
                )
        else:
            remote_id = str(client.send_message(conn.platform_target_id, text))
    except tg.TelegramError as exc:
        # The OLD publication stays untouched; only the new intent failed.
        _apply_remote_failure(
            db, new_pub, exc, operation=enums.PublicationOperation.REPOST
        )
        _attempt(
            db,
            publication=pub,
            operation=enums.PublicationOperation.REPOST,
            status=enums.AttemptStatus.FAILED,
            error_code=exc.code.value,
            error_detail="new message failed; old kept",
        )
        return {
            "publication": pub,
            "new_publication": new_pub,
            "plan": pubdomain.UpdatePlan.REPOST.value,
            "updated": False,
        }

    new_pub.remote_message_id = remote_id
    new_pub.remote_fingerprint = _fp(text)

    # Step 2: verify the new remote message really exists.
    try:
        checked = client.get_message(conn.platform_target_id, int(remote_id))
    except tg.TelegramError:
        checked = None
    if checked is None:
        # New message unverifiable: leave it UNKNOWN for reconciliation;
        # do NOT touch the old one (both may exist — safe).
        _transition(new_pub, enums.PublicationStatus.UNKNOWN_REMOTE_STATE)
        _attempt(
            db,
            publication=new_pub,
            operation=enums.PublicationOperation.REPOST,
            status=enums.AttemptStatus.UNKNOWN,
            error_detail="send ok, verification failed",
            remote_message_id=remote_id,
        )
        return {
            "publication": pub,
            "new_publication": new_pub,
            "plan": pubdomain.UpdatePlan.REPOST.value,
            "updated": True,
        }

    # Step 3: delete the OLD remote message (only now, after verification).
    # Bookkeeping order matters for the DB invariant (one PUBLISHED per
    # connection+product): the OLD leaves PUBLISHED before the new one
    # enters it. The REMOTE order stays new -> verify -> delete old.
    _delete_old_remote(db, pub=pub, conn=conn, client=client)

    _transition(new_pub, enums.PublicationStatus.PUBLISHED)
    _attempt(
        db,
        publication=new_pub,
        operation=enums.PublicationOperation.REPOST,
        status=enums.AttemptStatus.SUCCESS,
        remote_message_id=remote_id,
    )
    _attempt(
        db,
        publication=pub,
        operation=enums.PublicationOperation.REPOST,
        status=enums.AttemptStatus.SUCCESS,
    )
    AuditService(db).record(
        action="publication.reposted",
        actor_user_id=actor.user_id,
        business_id=business.business_id,
        target_type="publication",
        target_id=str(new_pub.publication_id),
        meta={"old": str(pub.publication_id), "product": str(product.product_id)},
    )
    db.flush()
    return {
        "publication": pub,
        "new_publication": new_pub,
        "plan": pubdomain.UpdatePlan.REPOST.value,
        "updated": True,
    }


def repost_publication(
    db: Session, *, business: Business, actor: User, publication_id: uuid.UUID
) -> dict:
    """Forced repost: publish a NEW remote message from the current content,
    verify it, then delete the old one (owner-approved safe order)."""
    pub = _get_publication(db, business=business, publication_id=publication_id)
    if pub.status != enums.PublicationStatus.PUBLISHED.value:
        raise ConflictError(
            f"only a PUBLISHED publication can be reposted (status: {pub.status})"
        )
    product = _get_product(db, business=business, product_id=pub.product_id)
    _require_publishable_product(db, product)
    conn = _get_connection(db, business=business, connection_id=pub.connection_id)
    if conn.status != enums.PlatformConnectionStatus.VERIFIED.value:
        raise ConflictError("the connection must be verified before publishing")
    caps = tg.TELEGRAM_CAPABILITIES
    text, media = _render_for_platform(
        db, business=business, product_id=product.product_id, caps=caps
    )
    return _execute_repost(
        db, business=business, actor=actor, pub=pub, text=text, media=media
    )


def delete_publication(
    db: Session, *, business: Business, actor: User, publication_id: uuid.UUID
) -> dict:
    """Delete the remote message (never the Product)."""
    pub = _get_publication(db, business=business, publication_id=publication_id)
    if pub.status != enums.PublicationStatus.PUBLISHED.value:
        raise ConflictError(
            f"only a PUBLISHED publication can be deleted (status: {pub.status})"
        )
    if not pub.remote_message_id:
        raise ConflictError("the publication has no remote message to delete")
    conn = db.get(PlatformConnection, pub.connection_id)
    _delete_old_remote(db, pub=pub, conn=conn, client=tg.get_telegram_client())
    if pub.status != enums.PublicationStatus.REMOTE_DELETED.value:
        return {"publication": pub, "deleted": False}
    _archive_post_if_empty(db, pub)
    AuditService(db).record(
        action="publication.deleted",
        actor_user_id=actor.user_id,
        business_id=business.business_id,
        target_type="publication",
        target_id=str(pub.publication_id),
        meta={"product": str(pub.product_id)},
    )
    db.flush()
    return {"publication": pub, "deleted": True}


def _archive_post_if_empty(db: Session, pub: Publication) -> None:
    post = db.get(Post, pub.post_id)
    remaining = db.scalars(
        select(Publication).where(
            Publication.post_id == post.post_id,
            Publication.publication_id != pub.publication_id,
            Publication.status == enums.PublicationStatus.PUBLISHED.value,
        )
    ).first()
    if remaining is None:
        post.status = enums.PostStatus.ARCHIVED.value


def check_publication(
    db: Session, *, business: Business, actor: User, publication_id: uuid.UUID
) -> dict:
    """Reconcile a publication against the remote (spec sections 15, 25)."""
    pub = _get_publication(db, business=business, publication_id=publication_id)
    if pub.status not in {
        enums.PublicationStatus.PUBLISHED.value,
        enums.PublicationStatus.UNKNOWN_REMOTE_STATE.value,
        enums.PublicationStatus.DISCONNECTED.value,
        enums.PublicationStatus.PERMISSION_LOST.value,
    }:
        raise ConflictError(
            f"only published/unknown/suspended publications can be checked (status: {pub.status})"
        )
    finding = _reconcile_remote(db, pub, client=tg.get_telegram_client())
    _attempt(
        db,
        publication=pub,
        operation=enums.PublicationOperation.RECONCILE,
        status=enums.AttemptStatus.SUCCESS,
        error_code=None,
    )
    AuditService(db).record(
        action="publication.reconciled",
        actor_user_id=actor.user_id,
        business_id=business.business_id,
        target_type="publication",
        target_id=str(pub.publication_id),
        meta={"status": pub.status, "remote_modified": pub.remote_modified},
    )
    db.flush()
    return {"publication": pub, "finding": finding}


def _reconcile_remote(
    db: Session, pub: Publication, *, client: tg.TelegramClient
) -> str:
    """Query the remote state and move the publication to a confirmed state.

    - UNKNOWN/RECONCILING -> PUBLISHED | REMOTE_DELETED (| stays UNKNOWN on
      transport errors);
    - PUBLISHED -> REMOTE_DELETED if the message vanished externally
      (recorded, never auto-reposted); a manually edited remote message is
      flagged ``remote_modified`` and never overwritten (spec section 17).
    """
    conn = db.get(PlatformConnection, pub.connection_id)
    if not pub.remote_message_id:
        # No remote identity to query (e.g. a publish timeout lost the
        # message id). V1 cannot recover the id without remote search:
        # keep the state UNKNOWN so the duplicate guard still holds and
        # the owner sees an explicit unresolved state — never a guessed
        # success or failure.
        _transition(pub, enums.PublicationStatus.RECONCILING)
        _transition(pub, enums.PublicationStatus.UNKNOWN_REMOTE_STATE)
        return "unresolved_no_remote_id"
    _transition(pub, enums.PublicationStatus.RECONCILING)
    try:
        message = client.get_message(
            conn.platform_target_id, int(pub.remote_message_id)
        )
    except tg.TelegramError as exc:
        if exc.code is enums.PublicationErrorCode.NOT_FOUND:
            message = None
        else:
            # Transport/permission problem: make the state recoverable
            # (RECONCILING -> UNKNOWN_REMOTE_STATE), never silently lost.
            _transition(pub, enums.PublicationStatus.UNKNOWN_REMOTE_STATE)
            pub.error_code = exc.code.value
            return f"unreachable:{exc.code.value}"
    if message is None:
        _transition(pub, enums.PublicationStatus.REMOTE_DELETED)
        _archive_post_if_empty(db, pub)
        return "remote_deleted"

    # The message exists. If a SIBLING is still PUBLISHED for the same
    # (connection, product), a repost that created THIS publication never
    # finished deleting the old message (crash between steps). Complete
    # that final step now — before this one may become PUBLISHED (DB
    # invariant: one PUBLISHED per connection+product).
    _complete_interrupted_repost(db, pub=pub, conn=conn, client=client)

    version = db.get(PostVersion, pub.post_version_id)
    remote_text = str(message.get("text") or message.get("caption") or "")
    if version is not None and _fp(remote_text) != version.content_fingerprint:
        pub.remote_modified = True
    _transition(pub, enums.PublicationStatus.PUBLISHED)
    return "published"


def _complete_interrupted_repost(
    db: Session, *, pub: Publication, conn: PlatformConnection, client: tg.TelegramClient
) -> None:
    """Complete the interrupted final step of a repost.

    If another PUBLISHED publication exists for the same (connection,
    product), its remote message must be deleted (the new one replaces it;
    the owner-approved order only pauses, never cancels, step 3).
    """
    sibling = db.scalars(
        select(Publication).where(
            Publication.connection_id == pub.connection_id,
            Publication.product_id == pub.product_id,
            Publication.publication_id != pub.publication_id,
            Publication.status == enums.PublicationStatus.PUBLISHED.value,
        )
    ).first()
    if sibling is None or not sibling.remote_message_id:
        return
    # `sibling` is the OLD message of an interrupted repost: delete it.
    _delete_old_remote(db, pub=sibling, conn=conn, client=client)


def list_publications(
    db: Session, *, business: Business, product_id: uuid.UUID | None = None
) -> list[Publication]:
    stmt = (
        select(Publication)
        .where(Publication.business_id == business.business_id)
        .order_by(Publication.created_at.desc())
    )
    if product_id is not None:
        stmt = stmt.where(Publication.product_id == product_id)
    return list(db.scalars(stmt).all())


def get_publication(
    db: Session, *, business: Business, publication_id: uuid.UUID
) -> Publication:
    return _get_publication(db, business=business, publication_id=publication_id)
