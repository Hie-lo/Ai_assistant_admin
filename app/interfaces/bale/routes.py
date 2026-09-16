"""Bale webhook routes (Phase 9).

Bale's Bot API mirrors Telegram's, so the webhook handling is similar.
Base URL: https://tapi.bale.ai/bot<token>/METHOD
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from app.config.settings import get_settings
from app.interfaces.bale.bot import BotMessage
from app.interfaces.telegram.bot import handle_message

logger = logging.getLogger("app.bale")
router = APIRouter(prefix="/api/bale", tags=["bale-bot"])


class BaleUpdate(BaseModel):
    update_id: int | None = None
    message: dict | None = None
    edited_message: dict | None = None


def _verify_bale_secret(
    x_bale_bot_api_secret_token: str | None = Header(
        default=None, alias="X-Bale-Bot-Api-Secret-Token"
    ),
    x_telegram_bot_api_secret_token: str | None = Header(
        default=None, alias="X-Telegram-Bot-Api-Secret-Token"
    ),
):
    """Enforce webhook secret if configured."""
    settings = get_settings()
    expected = settings.bale_webhook_secret
    if not expected:
        return True
    provided = x_bale_bot_api_secret_token or x_telegram_bot_api_secret_token
    if not provided:
        raise HTTPException(status_code=401, detail="missing webhook secret")
    if provided != expected:
        raise HTTPException(status_code=403, detail="invalid webhook secret")
    return True


@router.post("/webhook")
async def bale_webhook(
    request: Request,
    payload: BaleUpdate,
    _verified: bool = Depends(_verify_bale_secret),
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
        platform="BALE",
        platform_user_id=str(from_user["id"]),
        text=text,
        chat_id=str(chat.get("id")) if chat.get("id") else None,
        username=from_user.get("username"),
    )

    try:
        reply = handle_message(bot_msg)
    except Exception as exc:
        logger.exception("bale bot handler failed: %s", type(exc).__name__)
        return {"ok": True}

    if bot_msg.chat_id and reply.text:
        settings = get_settings()
        if settings.bale_bot_token:
            try:
                import httpx

                from app.infrastructure.platforms.bale import escape_markdown

                escaped = escape_markdown(reply.text)
                with httpx.Client(timeout=10) as http_client:
                    http_client.post(
                        f"{settings.bale_api_base_url}/bot{settings.bale_bot_token}/sendMessage",
                        json={
                            "chat_id": bot_msg.chat_id,
                            "text": escaped,
                        },
                    )
            except Exception as exc:
                logger.warning(
                    "failed to send bale reply: %s", type(exc).__name__
                )

    return {"ok": True, "reply": reply.text[:200] if reply.text else ""}


@router.get("/health")
def bale_health():
    settings = get_settings()
    return {
        "platform": "BALE",
        "configured": bool(settings.bale_bot_token),
        "webhook_secret_enforced": bool(settings.bale_webhook_secret),
        "webhook": "/api/bale/webhook",
    }
