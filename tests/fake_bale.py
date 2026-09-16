"""In-memory fake Bale client for Phase 6 tests.

Implements the semantic PlatformClient surface over a scriptable remote,
mirroring FakeTelegramClient plus the two platform-specific Bale behaviours
under test:

- **Markdown wire transform**: ``send_message``/``send_media_group`` store
  the ESCAPED text exactly as it went over the wire (Bale always
  markdown-parses, so the stored/returned text is the escaped form) — this
  is what ``remote_text_matches`` must tolerate.
- **48-hour delete limit**: every remote message records when it was sent
  on a fake clock (``now_hours``); ``delete_message`` fails with
  VALIDATION_ERROR for messages older than 48h (the real limit; the exact
  API error code is measured at live certification).
- **No message lookup**: ``get_message`` raises
  PLATFORM_UNSUPPORTED_OPERATION, exactly like the real adapter.

Inject with ``app.infrastructure.platforms.bale.set_bale_client_override``.
"""

from __future__ import annotations

from app.domain import enums
from app.infrastructure.platforms import bale as balemod
from app.infrastructure.platforms.bale import BaleError

CANONICAL_CHAT_ID = "-1009876543210"
BOT_ID = 777000222


class FakeBaleClient:
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
        #: Fake clock in hours; advance it to age messages past 48h.
        self.now_hours: float = 0.0
        #: (chat_id, message_id) -> {"text"/"caption", "media", "created_at"}
        self.remote: dict[tuple[str, int], dict] = {}
        self.next_id = 1
        self.fail_next: tuple[str, BaleError] | None = None
        self.always_fail: tuple[str, BaleError] | None = None
        self.calls: list[tuple[str, tuple, dict]] = []
        self._chat_ids: dict[str, str] = {}

    def chat_id_for(self, target: str) -> str:
        """Deterministic canonical chat id per target (idempotent on
        numeric ids, like the real getChat)."""
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
        self.fail_next = (method, BaleError(code, detail))

    def fail_always(
        self, method: str, code: enums.PublicationErrorCode, detail: str = "scripted"
    ) -> None:
        self.always_fail = (method, BaleError(code, detail))

    def _check_fail(self, method: str) -> None:
        if self.fail_next is not None and self.fail_next[0] == method:
            err = self.fail_next[1]
            self.fail_next = None
            raise err
        if self.always_fail is not None and self.always_fail[0] == method:
            raise self.always_fail[1]

    def _record(self, method: str, *args: object, **kwargs: object) -> None:
        self.calls.append((method, args, dict(kwargs)))

    # --- semantic surface (mirrors PlatformClient) ---

    def get_me(self) -> dict:
        self._record("get_me")
        self._check_fail("get_me")
        if not self.token_ok:
            raise BaleError(
                enums.PublicationErrorCode.AUTHENTICATION_ERROR, "bot token rejected"
            )
        return {"id": BOT_ID, "username": "shared_org_bot"}

    def get_chat(self, target: str) -> dict:
        self._record("get_chat", target)
        self._check_fail("get_chat")
        if not self.chat_exists:
            raise BaleError(enums.PublicationErrorCode.NOT_FOUND, "chat not found")
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
        # The wire text is what the adapter sent: escaped markdown.
        self.remote[(chat_id, mid)] = {
            "text": balemod.escape_markdown(text),
            "created_at": self.now_hours,
        }
        return mid

    def send_photo(self, chat_id: str, url: str, caption: str = "") -> int:
        self._record("send_photo", chat_id, url, caption)
        self._check_fail("send_photo")
        mid = self.next_id
        self.next_id += 1
        self.remote[(chat_id, mid)] = {
            "caption": balemod.escape_markdown(caption) or None,
            "media": [url],
            "created_at": self.now_hours,
        }
        return mid

    def send_media_group(self, chat_id: str, media_urls: list[str], caption: str = "") -> list[int]:
        self._record("send_media_group", chat_id, media_urls, caption)
        self._check_fail("send_media_group")
        ids = []
        for _ in media_urls:
            mid = self.next_id
            self.next_id += 1
            ids.append(mid)
        self.remote[(chat_id, ids[0])] = {
            "caption": balemod.escape_markdown(caption),
            "media": list(media_urls),
            "created_at": self.now_hours,
        }
        return ids

    def edit_message_text(self, chat_id: str, message_id: int, text: str) -> None:
        self._record("edit_message_text", chat_id, message_id, text)
        self._check_fail("edit_message_text")
        entry = self.remote.get((chat_id, message_id))
        if entry is None:
            raise BaleError(enums.PublicationErrorCode.NOT_FOUND, "message not found")
        entry["text"] = balemod.escape_markdown(text)
        entry.pop("caption", None)

    def edit_message_caption(self, chat_id: str, message_id: int, caption: str) -> None:
        self._record("edit_message_caption", chat_id, message_id, caption)
        self._check_fail("edit_message_caption")
        entry = self.remote.get((chat_id, message_id))
        if entry is None:
            raise BaleError(enums.PublicationErrorCode.NOT_FOUND, "message not found")
        entry["caption"] = balemod.escape_markdown(caption) or None

    def delete_message(self, chat_id: str, message_id: int) -> None:
        self._record("delete_message", chat_id, message_id)
        self._check_fail("delete_message")
        if (chat_id, message_id) not in self.remote:
            raise BaleError(enums.PublicationErrorCode.NOT_FOUND, "message not found")
        age = self.now_hours - self.remote[(chat_id, message_id)]["created_at"]
        if age >= 48:
            # Bale only allows deleting messages younger than 48h.
            raise BaleError(
                enums.PublicationErrorCode.VALIDATION_ERROR,
                "message is older than 48 hours and cannot be deleted",
            )
        del self.remote[(chat_id, message_id)]

    def get_message(self, chat_id: str, message_id: int) -> dict | None:
        self._record("get_message", chat_id, message_id)
        # Bale has NO message-lookup method — the real adapter raises the
        # same explicit error (never a silent fallback).
        raise BaleError(
            enums.PublicationErrorCode.PLATFORM_UNSUPPORTED_OPERATION,
            "Bale has no message-lookup method (inspection unsupported)",
        )

    # --- test assertion helpers ---

    def call_methods(self) -> list[str]:
        return [c[0] for c in self.calls]

    def remote_text(self, message_id: int) -> str:
        entry = self.remote.get((CANONICAL_CHAT_ID, message_id))
        if entry is None:
            return ""
        return str(entry.get("text") or entry.get("caption") or "")
