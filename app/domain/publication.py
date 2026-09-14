"""Publication state machine + idempotency + update planning (Phase 5
domain, pure).

Owner-approved V1 decisions (2026-09-15):
- manual publish only (scheduling lands in Phase 8);
- shared organization bot (platform-level credential, operational env);
- albums up to 10 images, text as caption;
- manual update trigger with automatic EDIT vs REPOST classification;
- remote manual deletion is recorded, never auto-reposted;
- repost order: publish new -> verify -> delete old.

Rules implemented here (Post & Publication spec sections 11-21, 25):
- a boolean "posted" is never the source of truth; explicit states only;
- UNKNOWN_REMOTE_STATE is not retryable by default — it must reconcile;
- a repost is a NEW publication, never a mutation of the old one;
- deterministic idempotency identity per (business, product, connection,
  post version, logical operation).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from app.domain import enums
from app.domain.enums import str_enum

S = enums.PublicationStatus

#: Allowed state transitions (from -> {to}). Everything else is invalid and
#: must fail closed (no silent state jumps).
ALLOWED_TRANSITIONS: dict[S, frozenset[S]] = {
    S.NOT_PUBLISHED: frozenset({S.QUEUED}),
    S.QUEUED: frozenset({S.PUBLISHING}),
    S.PUBLISHING: frozenset(
        {
            S.PUBLISHED,
            S.FAILED_RETRYABLE,
            S.FAILED_FINAL,
            S.UNKNOWN_REMOTE_STATE,
            S.DISCONNECTED,
            S.PERMISSION_LOST,
        }
    ),
    S.FAILED_RETRYABLE: frozenset(
        {S.QUEUED, S.FAILED_FINAL, S.DISCONNECTED, S.PERMISSION_LOST}
    ),
    S.UNKNOWN_REMOTE_STATE: frozenset(
        {S.RECONCILING, S.FAILED_FINAL, S.DISCONNECTED, S.PERMISSION_LOST}
    ),
    S.RECONCILING: frozenset(
        {S.PUBLISHED, S.REMOTE_DELETED, S.FAILED_FINAL, S.UNKNOWN_REMOTE_STATE}
    ),
    S.PUBLISHED: frozenset(
        {
            S.UPDATE_PENDING,
            S.REPOST_PENDING,
            S.DELETE_PENDING,
            S.RECONCILING,
            S.REMOTE_DELETED,
            S.PERMISSION_LOST,
            S.DISCONNECTED,
        }
    ),
    S.UPDATE_PENDING: frozenset({S.UPDATING}),
    S.UPDATING: frozenset(
        {
            S.PUBLISHED,
            S.FAILED_RETRYABLE,
            S.FAILED_FINAL,
            S.UNKNOWN_REMOTE_STATE,
            S.DISCONNECTED,
            S.PERMISSION_LOST,
        }
    ),
    S.REPOST_PENDING: frozenset({S.REPOSTING}),
    S.REPOSTING: frozenset(
        {
            S.PUBLISHED,
            S.FAILED_RETRYABLE,
            S.FAILED_FINAL,
            S.UNKNOWN_REMOTE_STATE,
            S.DISCONNECTED,
            S.PERMISSION_LOST,
        }
    ),
    S.DELETE_PENDING: frozenset({S.DELETING}),
    S.DELETING: frozenset(
        {
            S.REMOTE_DELETED,
            S.FAILED_RETRYABLE,
            S.FAILED_FINAL,
            S.UNKNOWN_REMOTE_STATE,
            S.DISCONNECTED,
            S.PERMISSION_LOST,
        }
    ),
    # Terminal-ish states that can still recover after a (re)verification.
    S.REMOTE_DELETED: frozenset(),
    S.DISCONNECTED: frozenset({S.PUBLISHED, S.RECONCILING}),
    S.PERMISSION_LOST: frozenset({S.PUBLISHED, S.RECONCILING, S.REMOTE_DELETED}),
    S.FAILED_FINAL: frozenset(),
}


def can_transition(current: S, target: S) -> bool:
    return target in ALLOWED_TRANSITIONS.get(current, frozenset())


def assert_transition(current: S, target: S) -> None:
    if not can_transition(current, target):
        raise InvalidPublicationTransition(f"{current.value} -> {target.value}")


class InvalidPublicationTransition(ValueError):
    """An illegal publication state transition (fail closed)."""


# --- Idempotency -----------------------------------------------------------


def publish_idempotency_key(
    *,
    business_id: str,
    product_id: str,
    connection_id: str,
    post_version_id: str,
) -> str:
    """Deterministic identity for a publish intent (spec section 14).

    Business + Product + Connection + PostVersion uniquely identifies the
    logical operation, so a duplicated request/job/worker resolves to the
    same key and is deduplicated (unique column backstop).
    """
    raw = f"publish|{business_id}|{product_id}|{connection_id}|{post_version_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def operation_idempotency_key(
    *,
    publication_id: str,
    operation: str,
    post_version_id: str,
    attempt_no: int,
) -> str:
    """Key for a single remote attempt (edit/delete/reconcile/repost)."""
    raw = f"{operation}|{publication_id}|{post_version_id}|{attempt_no}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# --- Update planning -------------------------------------------------------


class UpdatePlan(str_enum):
    NOOP = "NOOP"  # content unchanged
    EDIT = "EDIT"  # text/caption change, platform supports edit
    REPOST = "REPOST"  # media change (or edit unsupported) -> new message


@dataclass(frozen=True)
class ContentSnapshot:
    """Compact identity of a rendered PostVersion (for comparisons)."""

    content_fingerprint: str
    media_fingerprint: str


def plan_update(
    old: ContentSnapshot,
    new: ContentSnapshot,
    *,
    edit_supported: bool,
) -> UpdatePlan:
    """Classify what an update must do (spec section 18).

    - identical fingerprints -> NOOP (never touch the remote);
    - media unchanged + edit supported -> EDIT (text/caption in place);
    - media changed, or edit unsupported -> REPOST (publish new, verify,
      then delete old — owner-approved order).
    """
    if (
        old.content_fingerprint == new.content_fingerprint
        and old.media_fingerprint == new.media_fingerprint
    ):
        return UpdatePlan.NOOP
    if old.media_fingerprint == new.media_fingerprint and edit_supported:
        return UpdatePlan.EDIT
    return UpdatePlan.REPOST


# --- Error classification --------------------------------------------------


def is_retryable(error_code: str) -> bool:
    """Which taxonomy codes are safe to retry (bounded, by the caller)."""
    return error_code in {
        enums.PublicationErrorCode.NETWORK_TIMEOUT.value,
        enums.PublicationErrorCode.NETWORK_ERROR.value,
        enums.PublicationErrorCode.RATE_LIMITED.value,
        enums.PublicationErrorCode.REMOTE_UNKNOWN.value,
    }
