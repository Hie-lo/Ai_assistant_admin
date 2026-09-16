"""Bale management bot — business logic (Phase 9).

Bale's Bot API is based on Telegram's Bot API (PLATFORM_ADAPTER_SPECIFICATION_V1
section 5), so the management flow is identical to Telegram, but uses Bale's
endpoint and has its own token.

All business logic is shared with Telegram bot via a common handler factory.
The only differences are:
- Platform identifier (BALE vs TELEGRAM)
- Markdown escaping (Bale parses every message as markdown)
- Connection verification uses Bale's API

This module re-exports the Telegram handler logic with BALE platform.
"""

from __future__ import annotations

from app.interfaces.telegram.bot import BotMessage, BotReply, handle_message

# Re-use the same handler; the platform field distinguishes BALE vs TELEGRAM
# The escaping is handled at the transport layer (Bale adapter), not here.

__all__ = ["BotMessage", "BotReply", "handle_message"]


def handle_bale_message(platform_user_id: str, text: str, chat_id: str | None = None) -> BotReply:
    msg = BotMessage(
        platform="BALE",
        platform_user_id=str(platform_user_id),
        text=text,
        chat_id=chat_id,
    )
    return handle_message(msg)
