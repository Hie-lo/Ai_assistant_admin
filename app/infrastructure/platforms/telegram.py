"""Telegram adapter (Phase 5 infrastructure).

Isolates Telegram-specific behavior from the domain core (platform adapter
spec). The core requests semantic operations; this adapter translates them
to the verified Bot API behavior and normalizes errors into the platform
taxonomy.

Owner decision 2026-09-15: ONE shared organization bot (platform-level
credential from the environment). Each business adds the bot to its own
channel/group as admin and connects that target. The token is never stored
per business and never logged (rule 14: never log secrets).

Capability data lives in ``TelegramCapabilities`` instead of being
hard-coded across the core (adapter spec section 4).
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Protocol

import httpx

from app.domain import enums


class TelegramError(Exception):
    """Normalized Telegram failure carrying a taxonomy error code."""

    def __init__(self, code: enums.PublicationErrorCode, detail: str = "") -> None:
        super().__init__(detail or code.value)
        self.code = code
        self.detail = detail
        #: Seconds to wait before a rate-limited retry (0 otherwise).
        self.retry_after: float = 0.0


#: Verified Telegram Bot API limits (adapter spec section 4; official Bot API
#: documents sendMessage text 1-4096 after entity parsing).
@dataclass(frozen=True)
class TelegramCapabilities:
    platform: str = "TELEGRAM"
    send_text: bool = True
    send_single_media: bool = True
    send_media_group: bool = True
    edit_text: bool = True
    edit_caption: bool = True
    delete: bool = True
    inspect_remote: bool = True
    text_max_length: int = 4096
    caption_max_length: int = 1024
    media_group_max: int = 10


TELEGRAM_CAPABILITIES = TelegramCapabilities()


class TelegramClient(Protocol):
    """The semantic surface the domain may rely on (mockable)."""

    def get_me(self) -> dict: ...
    def get_chat(self, target: str) -> dict: ...
    def get_chat_member(self, target: str, user_id: int) -> dict: ...
    def send_message(self, chat_id: str, text: str) -> int: ...
    def send_media_group(
        self, chat_id: str, media_urls: list[str], caption: str = ""
    ) -> list[int]:
        ...
    def edit_message_text(self, chat_id: str, message_id: int, text: str) -> None: ...
    def edit_message_caption(self, chat_id: str, message_id: int, caption: str) -> None: ...
    def delete_message(self, chat_id: str, message_id: int) -> None: ...
    def get_message(self, chat_id: str, message_id: int) -> dict | None: ...


def _classify_http_error(response: httpx.Response) -> TelegramError:
    """Map a Bot API HTTP error to the taxonomy (adapter spec section 29).

    The response body is NOT returned to callers (it may echo secrets);
    only the status code and a short human diagnosis are kept.
    """
    status = response.status_code
    if status == 401:
        return TelegramError(enums.PublicationErrorCode.AUTHENTICATION_ERROR, "bot token rejected")
    if status == 403:
        return TelegramError(
            enums.PublicationErrorCode.PERMISSION_ERROR, "not permitted in this chat"
        )
    if status == 404:
        return TelegramError(enums.PublicationErrorCode.NOT_FOUND, "chat or message not found")
    if status == 429:
        retry_after = 3.0
        with contextlib.suppress(ValueError, AttributeError):
            retry_after = float(response.json().get("parameters", {}).get("retry_after", 3))
        err = TelegramError(enums.PublicationErrorCode.RATE_LIMITED, "rate limited")
        err.retry_after = retry_after
        return err
    if status == 400:
        return TelegramError(
            enums.PublicationErrorCode.VALIDATION_ERROR, "bot api rejected the request"
        )
    return TelegramError(enums.PublicationErrorCode.INTERNAL_ERROR, f"unexpected status {status}")


class HttpTelegramClient:
    """Real Bot API over HTTP. Token comes from settings (env), never logged."""

    def __init__(self, *, base_url: str, token: str, timeout: float = 30.0) -> None:
        if not token:
            raise TelegramError(
                enums.PublicationErrorCode.AUTHENTICATION_ERROR,
                "shared Telegram bot is not configured (missing token)",
            )
        self._base = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout

    def _call(self, method: str, payload: dict) -> object:
        url = f"{self._base}/bot{self._token}/{method}"
        try:
            response = httpx.post(url, json=payload, timeout=self._timeout)
        except httpx.TimeoutException as exc:
            raise TelegramError(
                enums.PublicationErrorCode.NETWORK_TIMEOUT, "telegram timeout"
            ) from exc
        except httpx.HTTPError as exc:
            raise TelegramError(
                enums.PublicationErrorCode.NETWORK_ERROR, exc.__class__.__name__
            ) from exc
        if response.status_code >= 400:
            raise _classify_http_error(response)
        data = response.json()
        if not data.get("ok", True):
            # ok:false with a 2xx/4xx body: classify defensively.
            desc = str(data.get("description", ""))
            code = enums.PublicationErrorCode.REMOTE_UNKNOWN
            if "not found" in desc.lower():
                code = enums.PublicationErrorCode.NOT_FOUND
            elif "flood" in desc.lower() or "limit" in desc.lower():
                code = enums.PublicationErrorCode.RATE_LIMITED
            raise TelegramError(code, desc)
        return data.get("result")

    # --- semantic surface ---

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
        result = self._call("sendMessage", {"chat_id": chat_id, "text": text})
        return int(result["message_id"])  # type: ignore[index]

    def send_media_group(self, chat_id: str, media_urls: list[str], caption: str = "") -> list[int]:
        media = [{"type": "photo", "media": url} for url in media_urls]
        payload: dict = {"chat_id": chat_id, "media": media}
        if caption:
            payload["caption"] = caption
        result = self._call("sendMediaGroup", payload)
        if not isinstance(result, list):
            raise TelegramError(
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
            {"chat_id": chat_id, "message_id": message_id, "text": text},
        )

    def edit_message_caption(self, chat_id: str, message_id: int, caption: str) -> None:
        self._call(
            "editMessageCaption",
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "caption": caption or None,
            },
        )

    def delete_message(self, chat_id: str, message_id: int) -> None:
        self._call("deleteMessage", {"chat_id": chat_id, "message_id": message_id})

    def get_message(self, chat_id: str, message_id: int) -> dict | None:
        try:
            result = self._call("getMessage", {"chat_id": chat_id, "message_id": message_id})
        except TelegramError as exc:
            if exc.code is enums.PublicationErrorCode.NOT_FOUND:
                return None
            raise
        return dict(result) if isinstance(result, dict) else None


_client_override: TelegramClient | None = None


def set_telegram_client_override(client: TelegramClient | None) -> None:
    """Test/dependency-injection seam (never reads the network in tests)."""
    global _client_override
    _client_override = client


def get_telegram_client() -> TelegramClient:
    """Resolve the shared organization bot client (settings-backed)."""
    if _client_override is not None:
        return _client_override
    from app.config.settings import get_settings

    settings = get_settings()
    return HttpTelegramClient(
        base_url=settings.telegram_api_base_url,
        token=settings.telegram_bot_token,
        timeout=settings.telegram_request_timeout_seconds,
    )
