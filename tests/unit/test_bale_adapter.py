"""Phase 6 unit: Bale adapter — markdown escaping, error classification,
HTTP transport contract (no network), and the inspection-unsupported rule.
"""

from __future__ import annotations

import hashlib

import httpx
import pytest
from app.domain import enums
from app.infrastructure.platforms import bale
from app.infrastructure.platforms import base as platforms

pytestmark = pytest.mark.unit


# --- markdown escaping --------------------------------------------------------


def test_escape_markdown_escapes_all_specials():
    assert bale.escape_markdown("*bold*") == "\\*bold\\*"
    assert bale.escape_markdown("_italic_") == "\\_italic\\_"
    assert bale.escape_markdown("[link](url)") == "\\[link\\]\\(url\\)"
    assert bale.escape_markdown("a\\b") == "a\\\\b"
    assert bale.escape_markdown("price: 2,000,000 [تومان] (cash)") == (
        "price: 2,000,000 \\[تومان\\] \\(cash\\)"
    )


def test_escape_markdown_passes_plain_text_through():
    text = "A fast laptop. 1000000 IRT. Stock: 3."
    assert bale.escape_markdown(text) == text
    assert bale.escape_markdown("") == ""


def test_escape_markdown_is_reversible_without_information_loss():
    # Escaping must be lossless: undoing it (\\ -> backslash, then
    # dropping the escape backslashes) restores the original exactly.
    original = "A *b* _c_ [d](e) \\ end"
    escaped = bale.escape_markdown(original)
    unescaped = escaped.replace("\\\\", "\x00").replace("\\", "")
    assert unescaped.replace("\x00", "\\") == original
    assert all(ch in escaped for ch in original)


def test_remote_text_matches_tolerates_escaped_wire_form():
    clean = "Price *3,000,000* (IRT)"
    fp = hashlib.sha256(clean.encode("utf-8")).hexdigest()
    escaped = bale.escape_markdown(clean)
    # Raw form (a platform that stores text verbatim).
    assert platforms.remote_text_matches("TELEGRAM", clean, fp) is True
    assert platforms.remote_text_matches("TELEGRAM", escaped, fp) is False
    # Bale: the remote returns the escaped form — must still match.
    assert platforms.remote_text_matches("BALE", clean, fp) is True
    assert platforms.remote_text_matches("BALE", escaped, fp) is True
    # A genuinely different text never matches.
    assert platforms.remote_text_matches("BALE", "something else", fp) is False


# --- capabilities -------------------------------------------------------------


def test_bale_capabilities_shape():
    caps = platforms.get_capabilities("BALE")
    assert caps.platform == "BALE"
    assert caps.text_max_length == 4096
    assert caps.single_media_caption_max_length == 4096
    assert caps.album_caption_max_length == 1024
    assert caps.media_group_max == 10
    assert caps.inspect_remote is False


def test_supported_platforms_are_telemark_and_bale():
    assert frozenset({"TELEGRAM", "BALE"}) == platforms.SUPPORTED_PLATFORMS


def test_get_platform_client_unknown_platform_raises_taxonomy():
    with pytest.raises(platforms.PlatformError) as excinfo:
        platforms.get_platform_client("EITAA")
    assert (
        excinfo.value.code
        is enums.PublicationErrorCode.PLATFORM_UNSUPPORTED_OPERATION
    )


# --- error classification ------------------------------------------------------


def test_classify_prefers_body_error_code():
    # Bale documents error_code in the JSON body; it wins over the status.
    err = bale._classify(200, 404)
    assert err.code is enums.PublicationErrorCode.NOT_FOUND
    err = bale._classify(500, 429)
    assert err.code is enums.PublicationErrorCode.RATE_LIMITED


def test_classify_falls_back_to_http_status():
    assert (
        bale._classify(401).code is enums.PublicationErrorCode.AUTHENTICATION_ERROR
    )
    assert bale._classify(403).code is enums.PublicationErrorCode.PERMISSION_ERROR
    assert bale._classify(404).code is enums.PublicationErrorCode.NOT_FOUND
    assert bale._classify(400).code is enums.PublicationErrorCode.VALIDATION_ERROR
    assert bale._classify(503).code is enums.PublicationErrorCode.INTERNAL_ERROR


def test_bale_error_is_a_platform_error():
    err = bale.BaleError(enums.PublicationErrorCode.PERMISSION_ERROR, "denied")
    assert isinstance(err, platforms.PlatformError)
    assert err.code is enums.PublicationErrorCode.PERMISSION_ERROR
    assert err.detail == "denied"
    assert err.retry_after == 0.0


# --- HTTP transport contract (no network) ---------------------------------------


def _client(monkeypatch, body: dict, status: int = 200) -> tuple[bale.HttpBaleClient, list]:
    """Patch httpx.post and capture (url, parsed json body) per call."""
    seen: list[tuple[str, dict]] = []

    def fake_post(url, json=None, timeout=None):
        request = httpx.Request("POST", url, json=json)
        seen.append((str(url), request.read() if json is None else dict(json)))
        return httpx.Response(status, request=request, json=body)

    monkeypatch.setattr(httpx, "post", fake_post)
    return bale.HttpBaleClient(
        base_url="https://tapi.bale.ai", token="123456:TEST"
    ), seen


def test_http_url_shape_and_json_payload(monkeypatch):
    client, seen = _client(
        monkeypatch, {"ok": True, "result": {"id": 777000222, "username": "bot"}}
    )
    me = client.get_me()
    assert me["username"] == "bot"
    assert len(seen) == 1
    assert seen[0][0] == "https://tapi.bale.ai/bot123456:TEST/getMe"


def test_send_message_escapes_on_the_wire(monkeypatch):
    client, seen = _client(
        monkeypatch, {"ok": True, "result": {"message_id": 41}}
    )
    mid = client.send_message("-100", "A *b* (c)")
    assert mid == 41
    payload = seen[0][1]
    assert payload["text"] == "A \\*b\\* \\(c\\)"
    assert payload["chat_id"] == "-100"


def test_send_media_group_captions_first_item_and_escaped(monkeypatch):
    client, seen = _client(
        monkeypatch,
        {"ok": True, "result": [{"message_id": 1}, {"message_id": 2}]},
    )
    ids = client.send_media_group(
        "-100", ["u1", "u2"], caption="two *photos*"
    )
    assert ids == [1, 2]
    payload = seen[0][1]
    items = payload["media"]  # native JSON array (live-verified wire form)
    assert isinstance(items, list)
    assert items[0]["caption"] == "two " + chr(92) + "*photos" + chr(92) + "*"
    assert "caption" not in items[1]
    assert items[0]["type"] == "photo"
    assert items[0]["media"] == "u1"


def test_send_media_group_wire_format_is_native_array(monkeypatch):
    # LIVE-VERIFIED REGRESSION (2026-09-15, certification run #5 diagnostic):
    # against the real API, a NATIVE JSON array in a JSON body was
    # ACCEPTED, while a JSON-serialized string in a JSON body was REJECTED
    # (400 "malformed request", run #4). The community SDKs' string
    # convention belongs to their form-encoded requests, not JSON bodies.
    client, seen = _client(
        monkeypatch,
        {"ok": True, "result": [{"message_id": 1}, {"message_id": 2}]},
    )
    client.send_media_group("-100", ["u1", "u2"], caption="c")
    payload = seen[0][1]
    assert isinstance(payload["media"], list), (
        "Bale (JSON body) expects media as a NATIVE JSON array, "
        "not a serialized string"
    )
    assert payload["media"][0] == {"type": "photo", "media": "u1", "caption": "c"}
    assert payload["media"][1] == {"type": "photo", "media": "u2"}


def test_ok_false_body_with_retry_after(monkeypatch):
    client, _ = _client(
        monkeypatch,
        {
            "ok": False,
            "error_code": 429,
            "description": "slow down",
            "parameters": {"retry_after": 5},
        },
    )
    with pytest.raises(bale.BaleError) as excinfo:
        client.get_me()
    assert excinfo.value.code is enums.PublicationErrorCode.RATE_LIMITED
    assert excinfo.value.retry_after == 5.0


def test_http_4xx_body_error_code_wins(monkeypatch):
    client, _ = _client(
        monkeypatch,
        {"ok": False, "error_code": 403, "description": "not admin"},
        status=500,
    )
    with pytest.raises(bale.BaleError) as excinfo:
        client.get_me()
    # The documented body error_code (403 -> PERMISSION) beats the status.
    assert excinfo.value.code is enums.PublicationErrorCode.PERMISSION_ERROR


def test_http_status_without_body_is_classified(monkeypatch):
    client, _ = _client(monkeypatch, {"ok": False, "description": "bad"}, status=401)
    with pytest.raises(bale.BaleError) as excinfo:
        client.get_me()
    assert excinfo.value.code is enums.PublicationErrorCode.AUTHENTICATION_ERROR


def test_timeout_and_network_errors(monkeypatch):
    def boom(url, json=None, timeout=None):
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(httpx, "post", boom)
    client = bale.HttpBaleClient(base_url="https://tapi.bale.ai", token="t")
    with pytest.raises(bale.BaleError) as excinfo:
        client.get_me()
    assert excinfo.value.code is enums.PublicationErrorCode.NETWORK_TIMEOUT

    def boom2(url, json=None, timeout=None):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "post", boom2)
    with pytest.raises(bale.BaleError) as excinfo:
        client.get_me()
    assert excinfo.value.code is enums.PublicationErrorCode.NETWORK_ERROR


def test_error_detail_includes_api_description(monkeypatch):
    # The API's own description must be surfaced (diagnosability) — it is
    # API text, never a secret.
    seen: list = []

    def fake_post(url, json=None, timeout=None):
        request = httpx.Request("POST", url, json=json)
        seen.append(request)
        return httpx.Response(
            500,
            request=request,
            json={"ok": False, "error_code": 400, "description": "media group is too large"},
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    client = bale.HttpBaleClient(base_url="https://tapi.bale.ai", token="t")
    with pytest.raises(bale.BaleError) as excinfo:
        client.send_media_group("-100", ["u1", "u2"], caption="c")
    # The body error_code (400) wins over the HTTP status (500)...
    assert excinfo.value.code is enums.PublicationErrorCode.VALIDATION_ERROR
    # ...and the API's own description is carried in the detail.
    assert "media group is too large" in excinfo.value.detail
    assert "error_code=400" in excinfo.value.detail


def test_error_detail_survives_non_json_body(monkeypatch):
    # A gateway-level 500 with no JSON body must not crash the client.
    def fake_post(url, json=None, timeout=None):
        request = httpx.Request("POST", url, json=json)
        return httpx.Response(500, request=request, text="<html>boom</html>")

    monkeypatch.setattr(httpx, "post", fake_post)
    client = bale.HttpBaleClient(base_url="https://tapi.bale.ai", token="t")
    with pytest.raises(bale.BaleError) as excinfo:
        client.get_me()
    assert excinfo.value.code is enums.PublicationErrorCode.INTERNAL_ERROR
    assert "500" in excinfo.value.detail


def test_error_description_is_bounded(monkeypatch):
    def fake_post(url, json=None, timeout=None):
        request = httpx.Request("POST", url, json=json)
        return httpx.Response(
            400,
            request=request,
            json={"ok": False, "error_code": 400, "description": "x" * 500},
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    client = bale.HttpBaleClient(base_url="https://tapi.bale.ai", token="t")
    with pytest.raises(bale.BaleError) as excinfo:
        client.get_me()
    assert len(excinfo.value.detail) <= 260


def test_http_client_refuses_missing_token_without_network():
    with pytest.raises(bale.BaleError) as excinfo:
        bale.HttpBaleClient(base_url="https://tapi.bale.ai", token="")
    assert excinfo.value.code is enums.PublicationErrorCode.AUTHENTICATION_ERROR


def test_get_message_is_explicitly_unsupported():
    client = bale.HttpBaleClient(base_url="https://tapi.bale.ai", token="t")
    with pytest.raises(bale.BaleError) as excinfo:
        client.get_message("-100", 1)
    assert (
        excinfo.value.code
        is enums.PublicationErrorCode.PLATFORM_UNSUPPORTED_OPERATION
    )


def test_override_seam_roundtrip():
    marker = object()
    bale.set_bale_client_override(marker)
    try:
        assert platforms.get_platform_client("BALE") is marker
    finally:
        bale.set_bale_client_override(None)
    with pytest.raises(bale.BaleError):
        # No token configured in tests -> the factory refuses.
        platforms.get_platform_client("BALE")
