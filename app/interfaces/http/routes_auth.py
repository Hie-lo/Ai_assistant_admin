"""Auth routes: register, login, logout, me, session management."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Request, Response
from sqlalchemy import select

from app.application import auth
from app.config.settings import get_settings
from app.domain import enums
from app.domain.errors import DomainError
from app.infrastructure.db.models import Business, Membership, UserSession
from app.interfaces.http import schemas
from app.interfaces.http.deps import SESSION_COOKIE, CurrentUser, Db

router = APIRouter(prefix="/api/v1", tags=["auth"])


def _settings():
    return get_settings()


@router.post("/auth/register", response_model=schemas.UserOut, status_code=201)
def register(payload: schemas.RegisterRequest, db: Db) -> schemas.UserOut:
    user = auth.register_user(
        db,
        email=payload.email,
        password=payload.password,
        display_name=payload.display_name,
    )
    return schemas.UserOut.model_validate(user)


@router.post("/auth/login", response_model=schemas.UserOut)
def login(
    payload: schemas.LoginRequest, db: Db, request: Request, response: Response
) -> schemas.UserOut:
    user, token = auth.authenticate(
        db,
        email=payload.email,
        password=payload.password,
        ip=request.client.host if request.client else None,
        user_agent=(request.headers.get("user-agent") or "")[:512] or None,
    )
    settings = _settings()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=settings.is_prod,
        max_age=int(auth.SESSION_TTL.total_seconds()),
        path="/",
    )
    return schemas.UserOut.model_validate(user)


@router.post("/auth/logout", status_code=204)
def logout(db: Db, request: Request, response: Response) -> None:
    resolved = auth.resolve_session(db, request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, path="/")
    if resolved is not None:
        user, session = resolved
        auth.revoke_session(db, session, actor_user_id=user.user_id)


@router.get("/me", response_model=schemas.MeOut)
def me(user: CurrentUser, db: Db) -> schemas.MeOut:
    memberships = (
        db.scalars(
            select(Membership).where(
                Membership.user_id == user.user_id,
                Membership.status == enums.MembershipStatus.ACTIVE.value,
            )
        ).all()
    )
    businesses = []
    for m in memberships:
        b = db.get(Business, m.business_id)
        if b is None:
            continue
        businesses.append(
            schemas.BusinessRef(
                business_id=b.business_id,
                name=b.business_name,
                business_type_key=b.business_type_key,
                role=m.role,
            )
        )
    return schemas.MeOut(user=schemas.UserOut.model_validate(user), businesses=businesses)


@router.get("/account/sessions", response_model=list[schemas.SessionOut])
def list_sessions(user: CurrentUser, db: Db) -> list[schemas.SessionOut]:
    rows = auth.list_user_sessions(db, user.user_id)
    out = []
    for s in rows:
        data = schemas.SessionOut.model_validate(s)
        data.is_active = s.revoked_at is None
        out.append(data)
    return out


@router.post("/account/sessions/{session_id}/revoke", status_code=204)
def revoke_session_route(user: CurrentUser, db: Db, session_id: uuid.UUID) -> None:
    row = db.get(UserSession, session_id)
    if row is None or row.user_id != user.user_id:
        raise DomainError("Session not found")
    auth.revoke_session(db, row, actor_user_id=user.user_id)
