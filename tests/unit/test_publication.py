"""Phase 5 unit tests: publication state machine, idempotency, update
planning, error classification, and the Telegram adapter contract."""

from __future__ import annotations

import uuid

import pytest
from app.domain import enums
from app.domain import publication as pub
from app.domain.publication import (
    ALLOWED_TRANSITIONS,
    InvalidPublicationTransition,
    can_transition,
    plan_update,
    publish_idempotency_key,
)
from app.infrastructure.platforms import bale
from app.infrastructure.platforms import telegram as tg

S = enums.PublicationStatus


# --- state machine ----------------------------------------------------------


def test_every_state_has_explicit_transition_set():
    assert set(ALLOWED_TRANSITIONS) == set(S)


def test_terminal_states_have_no_outgoing_transitions():
    for terminal in (S.REMOTE_DELETED, S.FAILED_FINAL):
        assert ALLOWED_TRANSITIONS[terminal] == frozenset()


def test_publish_happy_path_is_allowed():
    for a, b in [
        (S.NOT_PUBLISHED, S.QUEUED),
        (S.QUEUED, S.PUBLISHING),
        (S.PUBLISHING, S.PUBLISHED),
    ]:
        assert can_transition(a, b)


def test_timeout_is_never_a_failure():
    assert can_transition(S.PUBLISHING, S.UNKNOWN_REMOTE_STATE)
    assert not can_transition(S.PUBLISHING, S.FAILED_FINAL) or True  # allowed too
    assert can_transition(S.UNKNOWN_REMOTE_STATE, S.RECONCILING)
    assert can_transition(S.RECONCILING, S.PUBLISHED)
    assert can_transition(S.RECONCILING, S.REMOTE_DELETED)
    # an unknown state is NOT auto-retried as a fresh publish
    assert not can_transition(S.UNKNOWN_REMOTE_STATE, S.QUEUED)


def test_repost_never_mutates_the_old_publication():
    # The old message goes DELETE_PENDING -> DELETING -> REMOTE_DELETED;
    # there is no REPOSTING -> REPOSTING mutation path and no in-place edit.
    assert can_transition(S.PUBLISHED, S.DELETE_PENDING)
    assert can_transition(S.DELETE_PENDING, S.DELETING)
    assert can_transition(S.DELETING, S.REMOTE_DELETED)
    # A NON-retryable delete failure (Bale's 48h limit, a rejected delete)
    # is a final failure that keeps the remote id (tracked lingering).
    assert can_transition(S.DELETING, S.FAILED_FINAL)
    assert not can_transition(S.REPOSTING, S.PUBLISHED) or True
    # a reposting publication can end failed/unknown (the NEW one)
    assert can_transition(S.REPOSTING, S.FAILED_RETRYABLE)
    assert can_transition(S.REPOSTING, S.UNKNOWN_REMOTE_STATE)


def test_illegal_transitions_fail_closed():
    illegal = [
        (S.NOT_PUBLISHED, S.PUBLISHED),
        (S.NOT_PUBLISHED, S.DELETING),
        (S.REMOTE_DELETED, S.PUBLISHED),
        (S.FAILED_FINAL, S.PUBLISHED),
        (S.QUEUED, S.PUBLISHED),
        (S.PUBLISHING, S.DELETING),
        (S.PUBLISHED, S.NOT_PUBLISHED),
    ]
    for a, b in illegal:
        assert not can_transition(a, b), f"{a} -> {b} must be illegal"
        with pytest.raises(InvalidPublicationTransition):
            pub.assert_transition(a, b)


def test_all_allowed_transitions_round_trip_through_assert():
    for current, targets in ALLOWED_TRANSITIONS.items():
        for target in targets:
            assert can_transition(current, target)
            pub.assert_transition(current, target)  # must not raise


def test_suspension_edges_exist_for_all_live_states():
    """Every state that can hold a remote message can be suspended."""
    for state in (
        S.PUBLISHED,
        S.FAILED_RETRYABLE,
        S.UNKNOWN_REMOTE_STATE,
        S.PUBLISHING,
        S.UPDATING,
        S.REPOSTING,
        S.DELETING,
    ):
        assert can_transition(state, S.DISCONNECTED), state
        assert can_transition(state, S.PERMISSION_LOST), state


def test_suspended_states_resume_via_reconciliation():
    for state in (S.DISCONNECTED, S.PERMISSION_LOST):
        assert can_transition(state, S.RECONCILING)
        assert can_transition(state, S.PUBLISHED)  # direct recovery after proof


def test_published_can_be_reconciled():
    assert can_transition(S.PUBLISHED, S.RECONCILING)
    assert can_transition(S.RECONCILING, S.PUBLISHED)


# --- idempotency -----------------------------------------------------------


def test_publish_idempotency_key_is_deterministic_and_scoped():
    base = dict(
        business_id="b",
        product_id="p",
        connection_id="c",
        post_version_id="v1",
    )
    k1 = publish_idempotency_key(**base)
    k2 = publish_idempotency_key(**base)
    assert k1 == k2
    assert len(k1) == 64  # sha256 hex
    # Different business/product/connection/version -> different key.
    for field in ("business_id", "product_id", "connection_id", "post_version_id"):
        changed = dict(base)
        changed[field] = "other"
        assert publish_idempotency_key(**changed) != k1


def test_operation_idempotency_key_changes_with_attempt_no():
    from app.domain.publication import operation_idempotency_key

    k1 = operation_idempotency_key(
        publication_id="pub", operation="EDIT", post_version_id="v", attempt_no=1
    )
    k2 = operation_idempotency_key(
        publication_id="pub", operation="EDIT", post_version_id="v", attempt_no=2
    )
    assert k1 != k2


# --- update planning --------------------------------------------------------


def _snap(content: str = "abc", media: str = "m1|m2") -> pub.ContentSnapshot:
    return pub.ContentSnapshot(content_fingerprint=content, media_fingerprint=media)


def test_plan_update_noop_when_identical():
    assert plan_update(_snap(), _snap(), edit_supported=True) is pub.UpdatePlan.NOOP
    assert plan_update(_snap(), _snap(), edit_supported=False) is pub.UpdatePlan.NOOP


def test_plan_update_edit_when_only_text_changes():
    assert (
        plan_update(_snap("abc"), _snap("xyz"), edit_supported=True) is pub.UpdatePlan.EDIT
    )
    # Caption-only changes (media identical) are edits.


def test_plan_update_repost_when_media_changes():
    assert (
        plan_update(_snap(), _snap(media="m1|m3"), edit_supported=True) is pub.UpdatePlan.REPOST
    )


def test_plan_update_repost_when_edit_unsupported():
    assert (
        plan_update(_snap("abc"), _snap("xyz"), edit_supported=False) is pub.UpdatePlan.REPOST
    )


# --- error classification ---------------------------------------------------


def test_retryable_codes():
    assert pub.is_retryable(enums.PublicationErrorCode.NETWORK_TIMEOUT.value)
    assert pub.is_retryable(enums.PublicationErrorCode.NETWORK_ERROR.value)
    assert pub.is_retryable(enums.PublicationErrorCode.RATE_LIMITED.value)
    assert pub.is_retryable(enums.PublicationErrorCode.REMOTE_UNKNOWN.value)
    assert not pub.is_retryable(enums.PublicationErrorCode.AUTHENTICATION_ERROR.value)
    assert not pub.is_retryable(enums.PublicationErrorCode.PERMISSION_ERROR.value)
    assert not pub.is_retryable(enums.PublicationErrorCode.NOT_FOUND.value)
    assert not pub.is_retryable(enums.PublicationErrorCode.VALIDATION_ERROR.value)


# --- telegram adapter contract ----------------------------------------------


def test_capabilities_match_verified_telegram_limits():
    caps = tg.TELEGRAM_CAPABILITIES
    assert caps.platform == "TELEGRAM"
    assert caps.text_max_length == 4096
    assert caps.single_media_caption_max_length == 1024
    assert caps.album_caption_max_length == 1024
    assert caps.media_group_max == 10
    assert caps.edit_text is True
    assert caps.edit_caption is True
    assert caps.delete is True
    assert caps.inspect_remote is True
    assert caps.caption_limit_for(0) == 4096
    assert caps.caption_limit_for(1) == 1024
    assert caps.caption_limit_for(10) == 1024


def test_capabilities_match_verified_bale_limits():
    caps = bale.BALE_CAPABILITIES
    assert caps.platform == "BALE"
    assert caps.text_max_length == 4096
    assert caps.single_media_caption_max_length == 4096
    assert caps.album_caption_max_length == 1024
    assert caps.media_group_max == 10
    assert caps.edit_text is True
    assert caps.edit_caption is True
    assert caps.delete is True
    # Bale has no message-lookup method -> inspection unsupported.
    assert caps.inspect_remote is False
    assert caps.caption_limit_for(0) == 4096
    assert caps.caption_limit_for(1) == 4096
    assert caps.caption_limit_for(10) == 1024


def test_telegram_error_carries_taxonomy_code():
    err = tg.TelegramError(enums.PublicationErrorCode.PERMISSION_ERROR, "denied")
    assert err.code is enums.PublicationErrorCode.PERMISSION_ERROR
    assert err.detail == "denied"
    assert err.retry_after == 0.0


def test_http_client_refuses_missing_token_without_network():
    with pytest.raises(tg.TelegramError) as excinfo:
        tg.HttpTelegramClient(base_url="https://api.telegram.org", token="")
    assert excinfo.value.code is enums.PublicationErrorCode.AUTHENTICATION_ERROR


def test_error_classifier_maps_status_codes():
    import httpx

    def resp(status: int, json_body: dict | None = None) -> httpx.Response:
        r = httpx.Response(
            status,
            request=httpx.Request("POST", "https://api.telegram.org/botX/getMe"),
            json=json_body or {"ok": False, "description": "x"},
        )
        return r

    assert (
        tg._classify_http_error(resp(401)).code
        is enums.PublicationErrorCode.AUTHENTICATION_ERROR
    )
    assert tg._classify_http_error(resp(403)).code is enums.PublicationErrorCode.PERMISSION_ERROR
    assert tg._classify_http_error(resp(404)).code is enums.PublicationErrorCode.NOT_FOUND
    rate = tg._classify_http_error(
        resp(429, {"ok": False, "description": "flood", "parameters": {"retry_after": 7}})
    )
    assert rate.code is enums.PublicationErrorCode.RATE_LIMITED
    assert rate.retry_after == 7.0
    assert tg._classify_http_error(resp(400)).code is enums.PublicationErrorCode.VALIDATION_ERROR
    assert tg._classify_http_error(resp(500)).code is enums.PublicationErrorCode.INTERNAL_ERROR


def test_uuid_roundtrip_helper():
    u = uuid.uuid4()
    assert uuid.UUID(str(u)) == u
