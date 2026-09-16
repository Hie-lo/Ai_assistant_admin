"""In-memory fake Telegram client for Phase 5 tests.

Implements the semantic TelegramClient surface over a scriptable remote:
- remote messages live in ``remote[(chat_id, message_id)]``;
- individual calls can be scripted to fail with a specific taxonomy error
  (``fail_next``) or to always fail (``always_fail``);
- verification flags simulate bot token validity, chat existence and
  administrator status (the three-step verification);
- every call is recorded in ``calls`` for assertions.

Inject with ``app.infrastructure.platforms.telegram.set_telegram_client_override``.
"""

from __future__ import annotations

from app.domain import enums
from app.infrastructure.platforms.telegram import TelegramError

CANONICAL_CHAT_ID = "-1009876543210"
BOT_ID = 777000111


class FakeTelegramClient:
    def __init__(
        self,
        *,
        token_ok: bool = True,
        chat_exists: bool = True,
        is_admin: bool = True,
    ) -> None:
        self.token_ok = token_ok
        self.chat_exists = chat_exists
        self.is_admin = is_admin
        #: (chat_id, message_id) -> {"text": str} or {"caption": str, "media": [...]}
        self.remote: dict[tuple[str, int], dict] = {}
        self.next_id = 1
        self.fail_next: tuple[str, TelegramError] | None = None
        self.always_fail: tuple[str, TelegramError] | None = None
        self.calls: list[tuple[str, tuple, dict]] = []
        self._chat_ids: dict[str, str] = {}

    def chat_id_for(self, target: str) -> str:
        """Deterministic, distinct canonical chat id per target.

        The first connected target uses CANONICAL_CHAT_ID so single-target
        tests can address the remote directly. Numeric chat ids are
        already canonical and pass through unchanged (idempotent — real
        Telegram's getChat(numeric id) returns the same id).
        """
        if target.lstrip("-").isdigit():
            return target
        if target not in self._chat_ids:
            n = len(self._chat_ids)
            self._chat_ids[target] = (
                CANONICAL_CHAT_ID if n == 0 else f"-10098765432{10 + n}"
            )
        return self._chat_ids[target]

    # --- scripting helpers ---

    def fail_next_call(
        self, method: str, code: enums.PublicationErrorCode, detail: str = "scripted"
    ) -> None:
        self.fail_next = (method, TelegramError(code, detail))

    def fail_always(
        self, method: str, code: enums.PublicationErrorCode, detail: str = "scripted"
    ) -> None:
        self.always_fail = (method, TelegramError(code, detail))

    def _check_fail(self, method: str) -> None:
        if self.fail_next is not None and self.fail_next[0] == method:
            err = self.fail_next[1]
            self.fail_next = None
            raise err
        if self.always_fail is not None and self.always_fail[0] == method:
            raise self.always_fail[1]

    def _record(self, method: str, *args: object, **kwargs: object) -> None:
        self.calls.append((method, args, dict(kwargs)))

    # --- semantic surface (mirrors TelegramClient) ---

    def get_me(self) -> dict:
        self._record("get_me")
        self._check_fail("get_me")
        if not self.token_ok:
            raise TelegramError(
                enums.PublicationErrorCode.AUTHENTICATION_ERROR, "bot token rejected"
            )
        return {"id": BOT_ID, "username": "shared_org_bot"}

    def get_chat(self, target: str) -> dict:
        self._record("get_chat", target)
        self._check_fail("get_chat")
        if not self.chat_exists:
            raise TelegramError(enums.PublicationErrorCode.NOT_FOUND, "chat not found")
        return {"id": self.chat_id_for(target), "title": f"chat {target}"}

    def get_chat_member(self, chat_id: str, user_id: int) -> dict:
        self._record("get_chat_member", chat_id, user_id)
        self._check_fail("get_chat_member")
        status = "administrator" if self.is_admin else "member"
        return {"user": {"id": user_id}, "status": status}

    def send_message(self, chat_id: str, text: str) -> int:
        self._record("send_message", chat_id, text)
        self._check_fail("send_message")
        mid = self.next_id
        self.next_id += 1
        self.remote[(chat_id, mid)] = {"text": text}
        return mid

    def send_photo(self, chat_id: str, url: str, caption: str = "") -> int:
        self._record("send_photo", chat_id, url, caption)
        self._check_fail("send_photo")
        mid = self.next_id
        self.next_id += 1
        self.remote[(chat_id, mid)] = {"caption": caption, "media": [url]}
        return mid

    def send_media_group(self, chat_id: str, media_urls: list[str], caption: str = "") -> list[int]:
        self._record("send_media_group", chat_id, media_urls, caption)
        self._check_fail("send_media_group")
        ids = []
        for _ in media_urls:
            mid = self.next_id
            self.next_id += 1
            ids.append(mid)
        self.remote[(chat_id, ids[0])] = {"caption": caption, "media": list(media_urls)}
        return ids

    def edit_message_text(self, chat_id: str, message_id: int, text: str) -> None:
        self._record("edit_message_text", chat_id, message_id, text)
        self._check_fail("edit_message_text")
        entry = self.remote.get((chat_id, message_id))
        if entry is None:
            raise TelegramError(enums.PublicationErrorCode.NOT_FOUND, "message not found")
        entry["text"] = text
        entry.pop("caption", None)

    def edit_message_caption(self, chat_id: str, message_id: int, caption: str) -> None:
        self._record("edit_message_caption", chat_id, message_id, caption)
        self._check_fail("edit_message_caption")
        entry = self.remote.get((chat_id, message_id))
        if entry is None:
            raise TelegramError(enums.PublicationErrorCode.NOT_FOUND, "message not found")
        entry["caption"] = caption

    def delete_message(self, chat_id: str, message_id: int) -> None:
        self._record("delete_message", chat_id, message_id)
        self._check_fail("delete_message")
        if (chat_id, message_id) not in self.remote:
            raise TelegramError(enums.PublicationErrorCode.NOT_FOUND, "message not found")
        del self.remote[(chat_id, message_id)]

    def get_message(self, chat_id: str, message_id: int) -> dict | None:
        self._record("get_message", chat_id, message_id)
        self._check_fail("get_message")
        entry = self.remote.get((chat_id, message_id))
        if entry is None:
            return None
        return dict(entry)

    # --- test assertions helpers ---

    def call_methods(self) -> list[str]:
        return [c[0] for c in self.calls]

    def remote_text(self, message_id: int) -> str:
        entry = self.remote.get((CANONICAL_CHAT_ID, message_id))
        if entry is None:
            return ""
        return str(entry.get("text") or entry.get("caption") or "")
