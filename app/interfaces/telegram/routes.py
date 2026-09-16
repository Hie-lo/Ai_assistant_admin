"""Telegram webhook routes (Phase 9)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel

from app.config.settings import get_settings
from app.interfaces.telegram.bot import BotMessage, handle_message

logger = logging.getLogger("app.telegram")
router = APIRouter(prefix="/api/telegram", tags=["telegram-bot"])


class TelegramUpdate(BaseModel):
    update_id: int | None = None
    message: dict | None = None
    edited_message: dict | None = None


def _verify_telegram_secret(
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    return True


@router.post("/webhook")
async def telegram_webhook(
    request: Request,
    payload: TelegramUpdate,
    _verified: bool = Depends(_verify_telegram_secret),
):
    msg_data = payload.message or payload.edited_message
    if not msg_data:
        return {"ok": True}

    chat = msg_data.get("chat", {})
    from_user = msg_data.get("from", {})
    text = msg_data.get("text", "")

    if not text or not from_user.get("id"):
        return {"ok": True}

    bot_msg = BotMessage(
        platform="TELEGRAM",
        platform_user_id=str(from_user["id"]),
        text=text,
        chat_id=str(chat.get("id")) if chat.get("id") else None,
        username=from_user.get("username"),
    )

    try:
        reply = handle_message(bot_msg)
    except Exception as exc:
        logger.exception("telegram bot handler failed: %s", type(exc).__name__)
        return {"ok": True}

    if bot_msg.chat_id and reply.text:
        settings = get_settings()
        if settings.telegram_bot_token:
            try:
                import httpx

                base = settings.telegram_api_base_url
                token = settings.telegram_bot_token
                url = f"{base}/bot{token}/sendMessage"
                with httpx.Client(timeout=10) as http_client:
                    http_client.post(
                        url,
                        json={
                            "chat_id": bot_msg.chat_id,
                            "text": reply.text,
                        },
                    )
            except Exception as exc:
                logger.warning(
                    "failed to send telegram reply: %s",
                    type(exc).__name__,
                )

    return {"ok": True, "reply": reply.text[:200] if reply.text else ""}


@router.get("/health")
def telegram_health():
    settings = get_settings()
    return {
        "platform": "TELEGRAM",
        "configured": bool(settings.telegram_bot_token),
        "webhook": "/api/telegram/webhook",
    }
