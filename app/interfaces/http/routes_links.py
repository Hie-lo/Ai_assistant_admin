"""Account linking routes.

- ``POST /account/links``: authenticated user creates a one-time code for a
  platform (shown once; the digest is stored).
- ``POST /links/verify``: INTERNAL contract for the Telegram/Bale bot
  interface (Phase 9). Protected by a shared internal token header — the
  bot authenticates with it, never with a user cookie.
"""

from __future__ import annotations

from fastapi import APIRouter, Header

from app.application import linking
from app.config.settings import get_settings
from app.domain.errors import AuthenticationError
from app.interfaces.http import schemas
from app.interfaces.http.deps import CurrentUser, Db

router = APIRouter(prefix="/api/v1", tags=["links"])


@router.post("/account/links", response_model=schemas.LinkCodeOut, status_code=201)
def create_link_code(
    payload: schemas.CreateLinkCodeRequest, user: CurrentUser, db: Db
) -> schemas.LinkCodeOut:
    row, code = linking.create_link_code(db, user=user, platform=payload.platform)
    return schemas.LinkCodeOut(code=code, platform=row.platform, expires_at=row.expires_at)


@router.get("/account/identities", response_model=list[schemas.IdentityOut])
def list_identities(user: CurrentUser, db: Db) -> list[schemas.IdentityOut]:
    return [schemas.IdentityOut.model_validate(i) for i in linking.list_identities(db, user=user)]


@router.post("/links/verify", response_model=schemas.VerifyLinkCodeOut)
def verify_link_code(
    payload: schemas.VerifyLinkCodeRequest,
    db: Db,
    x_internal_token: str | None = Header(default=None),
) -> schemas.VerifyLinkCodeOut:
    settings = get_settings()
    expected = settings.internal_api_token
    if not expected or x_internal_token != expected:
        raise AuthenticationError("Internal token invalid")
    user = linking.verify_link_code(
        db, platform=payload.platform, code=payload.code, platform_user_id=payload.platform_user_id
    )
    return schemas.VerifyLinkCodeOut(user_id=user.user_id)
