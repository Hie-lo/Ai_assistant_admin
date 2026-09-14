"""Bale adapter (Phase 6 infrastructure).

Bale's Bot API is based on the Telegram Bot API with changes (verified
against the official docs, docs.bale.ai, 2026-09-15):

- Base URL: https://tapi.bale.ai/bot<token>/METHOD (GET/POST, JSON).
- Responses: {"ok": true, "result": ...} or {"ok": false, "error_code":
  int, "description": ..., "parameters": {"retry_after": n}}.
- sendMessage/editMessageText: 1-4096 chars; single-media caption 0-4096;
  album item caption 0-1024; sendMediaGroup documented (max items not
  documented -> conservative 10 until live certification measures it).
- deleteMessage: a message can only be deleted within 48h of being sent
  (older messages fail with a tracked error, never silently lost).
- getChatMember: bot must be admin; status "creator"/"administrator".
- Chat/User ids may exceed 32-bit (52-bit documented) -> stored as strings.
- **No message-lookup method exists** -> inspect_remote = False; the
  publication service must not pretend to inspect (adapter spec section 8).
- **All text is ALWAYS markdown-parsed** -> the adapter escapes special
  characters so product content renders verbatim (owner decision
  2026-09-15).

Owner decision 2026-09-15: ONE shared organization bot (platform-level
credential from the environment), same model as Telegram. The token is
never stored per business and never logged (rule 14).
"""

from __future__ import annotations

import contextlib
import json

import httpx

from app.domain import enums
from app.infrastructure.platforms.base import PlatformCapabilities, PlatformError

__all__ = [
    "BALE_CAPABILITIES",
    "BaleError",
    "HttpBaleClient",
    "build_client",
    "escape_markdown",
    "get_bale_client",
    "unescape_markdown",
    "set_bale_client_override",
]


class BaleError(PlatformError):
    """Normalized Bale failure carrying a taxonomy error code."""

    def __init__(self, code: enums.PublicationErrorCode, detail: str = "") -> None:
        super().__init__(code, detail)


#: Verified limits from the official Bale docs (2026-09-15). Album max is
#: the conservative Telegram-equivalent until live certification measures
#: Bale's own limit (certification checklist: "limits measured/confirmed").
BALE_CAPABILITIES = PlatformCapabilities(
    platform="BALE",
    text_max_length=4096,
    single_media_caption_max_length=4096,
    album_caption_max_length=1024,
    media_group_max=10,
    inspect_remote=False,  # Bale has no message-lookup method
)

#: Markdown specials used by Bale's always-on markdown parsing.
_MD_SPECIALS = frozenset("\\*[]()_")


def escape_markdown(text: str) -> str:
    """Escape markdown specials so Bale renders the text verbatim.

    Bale parses every message as markdown (bold ``*x*``, italic ``_x_``,
    links ``[t](u)``). Product content containing those characters would
    otherwise be reinterpreted or rejected; escaping keeps the published
    text exactly what the owner approved.
    """
    if not text:
        return text
    if not any(ch in _MD_SPECIALS for ch in text):
        return text
    out: list[str] = []
    for ch in text:
        out.append("\\" + ch if ch in _MD_SPECIALS else ch)
    return "".join(out)


def unescape_markdown(text: str) -> str:
    """Exact inverse of :func:`escape_markdown` (for wire->clean comparison).

    The remote stores what the bot SENT (the escaped form). Reconciliation
    compares the remote text against the fingerprint of the CLEAN text, so
    the escaped form is unescaped first: ``unescape(escape(x)) == x`` for
    all ``x``.
    """
    if not text:
        return text
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n and text[i + 1] in _MD_SPECIALS:
            out.append(text[i + 1])
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _attach_detail(err: BaleError, data: dict) -> None:
    """Surface the API's own ``description`` for diagnosability.

    Bale's description text is API text (never contains the token or the
    request URL), so it is safe to carry into error details and operator
    output (rule 14: never log secrets — this is not a secret). Bounded
    to 200 chars.
    """
    desc = str(data.get("description") or "").strip()
    if desc:
        suffix = desc[:200]
        err.detail = f"{err.detail} ({suffix})" if err.detail else suffix
    code_no = data.get("error_code")
    if code_no not in (None, "") and "error_code" not in err.detail:
        tag = f" [error_code={code_no}]"
        err.detail = f"{err.detail}{tag}" if err.detail else tag.lstrip()


def _classify(status: int, error_code: int | None = None) -> BaleError:
    """Map a Bale failure (HTTP status and/or body error_code) to taxonomy.

    Bale documents ``error_code`` in the JSON body; some clients may also
    see HTTP statuses. The body code wins when present (documented
    contract); HTTP status is the fallback.
    """
    code_no = error_code if error_code else status
    if code_no == 401:
        return BaleError(enums.PublicationErrorCode.AUTHENTICATION_ERROR, "bot token rejected")
    if code_no == 403:
        return BaleError(enums.PublicationErrorCode.PERMISSION_ERROR, "not permitted in this chat")
    if code_no == 404:
        return BaleError(enums.PublicationErrorCode.NOT_FOUND, "chat or message not found")
    if code_no == 429:
        return BaleError(enums.PublicationErrorCode.RATE_LIMITED, "rate limited")
    if code_no == 400:
        return BaleError(
            enums.PublicationErrorCode.VALIDATION_ERROR, "bale api rejected the request"
        )
    return BaleError(enums.PublicationErrorCode.INTERNAL_ERROR, f"unexpected status {code_no}")


class HttpBaleClient:
    """Real Bale Bot API over HTTP. Token comes from settings (env), never logged."""

    def __init__(self, *, base_url: str, token: str, timeout: float = 30.0) -> None:
        if not token:
            raise BaleError(
                enums.PublicationErrorCode.AUTHENTICATION_ERROR,
                "shared Bale bot is not configured (missing token)",
            )
        self._base = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout

    def _call(self, method: str, payload: dict) -> object:
        url = f"{self._base}/bot{self._token}/{method}"
        try:
            response = httpx.post(url, json=payload, timeout=self._timeout)
        except httpx.TimeoutException as exc:
            raise BaleError(
                enums.PublicationErrorCode.NETWORK_TIMEOUT, "bale timeout"
            ) from exc
        except httpx.HTTPError as exc:
            raise BaleError(
                enums.PublicationErrorCode.NETWORK_ERROR, exc.__class__.__name__
            ) from exc

        try:
            data = response.json()
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}

        error_code: int | None = None
        with contextlib.suppress(ValueError, TypeError):
            error_code = int(data.get("error_code"))

        if data.get("ok") is False:
            # ok:false with a body error_code (Bale's documented contract).
            err = _classify(response.status_code, error_code)
            _attach_detail(err, data)
            with contextlib.suppress(ValueError, AttributeError, TypeError):
                err.retry_after = float(data.get("parameters", {}).get("retry_after", 0))
            raise err
        if response.status_code >= 400:
            err = _classify(response.status_code, error_code)
            _attach_detail(err, data)
            raise err
        return data.get("result")

    # --- semantic surface (mirrors PlatformClient) ---

    def get_me(self) -> dict:
        result = self._call("getMe", {})
        return dict(result) if isinstance(result, dict) else {}

    def get_chat(self, target: str) -> dict:
        result = self._call("getChat", {"chat_id": target})
        return dict(result) if isinstance(result, dict) else {}

    def get_chat_member(self, target: str, user_id: int) -> dict:
        result = self._call("getChatMember", {"chat_id": target, "user_id": user_id})
        return dict(result) if isinstance(result, dict) else {}

    def send_message(self, chat_id: str, text: str) -> int:
        result = self._call("sendMessage", {"chat_id": chat_id, "text": escape_markdown(text)})
        return int(result["message_id"])  # type: ignore[index]

    def send_photo(self, chat_id: str, url: str, caption: str = "") -> int:
        # sendPhoto (single media) allows a 4096-char caption, unlike
        # album items (1024) — the service routes single media here so
        # BALE_CAPABILITIES.single_media_caption_max_length is real.
        payload: dict = {"chat_id": chat_id, "photo": url}
        if caption:
            payload["caption"] = escape_markdown(caption)
        result = self._call("sendPhoto", payload)
        return int(result["message_id"])  # type: ignore[index]

    def send_media_group(self, chat_id: str, media_urls: list[str], caption: str = "") -> list[int]:
        # The caption rides on the first album item (per-item limit 1024 —
        # the renderer already enforces the album caption limit).
        media = []
        for i, url in enumerate(media_urls):
            item: dict = {"type": "photo", "media": url}
            if i == 0 and caption:
                item["caption"] = escape_markdown(caption)
            media.append(item)
        # Bale documents ``media`` as a "JSON-serialized array" (same
        # wording as reply_markup): the wire value is a JSON STRING, not a
        # native array — verified against the official docs and both
        # working community SDKs (Go: json.Marshal into the param; Python
        # fork: json-encoded string). A native array in a JSON body is
        # rejected with 400 "malformed request" (live certification
        # finding, 2026-09-15).
        result = self._call(
            "sendMediaGroup", {"chat_id": chat_id, "media": json.dumps(media)}
        )
        if not isinstance(result, list):
            raise BaleError(
                enums.PublicationErrorCode.REMOTE_UNKNOWN, "unexpected media group result"
            )
        ids: list[int] = []
        for item in result:
            if isinstance(item, dict) and "message_id" in item:
                ids.append(int(item["message_id"]))
        return ids

    def edit_message_text(self, chat_id: str, message_id: int, text: str) -> None:
        self._call(
            "editMessageText",
            {"chat_id": chat_id, "message_id": message_id, "text": escape_markdown(text)},
        )

    def edit_message_caption(self, chat_id: str, message_id: int, caption: str) -> None:
        self._call(
            "editMessageCaption",
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "caption": escape_markdown(caption) or None,
            },
        )

    def delete_message(self, chat_id: str, message_id: int) -> None:
        # Bale only allows deleting messages younger than 48h; older
        # messages raise an API error which the service records as a
        # tracked lingering state (owner decision 2026-09-15).
        self._call("deleteMessage", {"chat_id": chat_id, "message_id": message_id})

    def get_message(self, chat_id: str, message_id: int) -> dict | None:
        # Bale has NO message-lookup method. Fail explicitly instead of
        # pretending (adapter spec section 8: no silent capability
        # fallback); callers check capabilities.inspect_remote first.
        raise BaleError(
            enums.PublicationErrorCode.PLATFORM_UNSUPPORTED_OPERATION,
            "Bale has no message-lookup method (inspection unsupported)",
        )


def build_client():
    """Factory used by the platform registry (settings-backed)."""
    from app.config.settings import get_settings

    settings = get_settings()
    return HttpBaleClient(
        base_url=settings.bale_api_base_url,
        token=settings.bale_bot_token,
        timeout=settings.bale_request_timeout_seconds,
    )


def set_bale_client_override(client: object | None) -> None:
    """Test/dependency-injection seam (never reads the network in tests)."""
    from app.infrastructure.platforms import base as _base

    _base.set_platform_client_override("BALE", client)  # type: ignore[arg-type]


def get_bale_client():
    """Resolve the shared organization bot client (settings-backed)."""
    from app.infrastructure.platforms import base as _base

    return _base.get_platform_client("BALE")
