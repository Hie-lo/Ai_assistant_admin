"""Request-scoped dependencies: DB session, current user, business scope.

Authorization is ALWAYS server-side: the cookie only identifies a session;
every protected operation re-resolves the active Business context and the
membership/permission (RBAC spec section 13). Cross-business or missing
context fails closed.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Generator
from typing import Annotated

from fastapi import Depends, Path, Request
from sqlalchemy.orm import Session

from app.application import auth
from app.application.audit import set_correlation_id
from app.application.authorization import has_permission
from app.application.business import require_business_access
from app.config.settings import get_settings
from app.domain.errors import AuthenticationError, AuthorizationError
from app.infrastructure.db.models import Business, Membership, User
from app.infrastructure.db.session import get_session_factory

SESSION_COOKIE = get_settings().session_cookie_name


def db_session() -> Generator[Session]:
    """Yield a request-scoped session; commit on success, roll back on error."""
    factory = get_session_factory()
    db = factory()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


Db = Annotated[Session, Depends(db_session)]


def apply_correlation_id(request: Request, correlation_id: str | None = None) -> str:
    """Adopt the inbound X-Correlation-Id (or mint one) for this request."""
    value = correlation_id or uuid.uuid4().hex
    request.state.correlation_id = value[:64]
    set_correlation_id(request.state.correlation_id)
    return request.state.correlation_id


def current_user(request: Request, db: Db) -> User:
    """Resolve the authenticated user from the session cookie, or 401.

    Reads cookie directly via request.cookies (robust) instead of Cookie()
    dependency which proved fragile under duplicate Cookie headers /
    0.0.0.0 host in docker+uvicorn (same root cause as web login loop).
    """
    token = request.cookies.get(SESSION_COOKIE) or request.cookies.get(
        get_settings().session_cookie_name
    )
    resolved = auth.resolve_session(db, token)
    if resolved is None:
        raise AuthenticationError("Not authenticated")
    user, _session = resolved
    request.state.user_id = user.user_id
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def require_super_admin(user: CurrentUser) -> User:
    """Platform operator gate (Phase 2).

    Independent of Business membership: super admins manage the plan
    catalog and verify manual payments across businesses (owner decision
    2026-09-13). A missing/revoked flag fails closed immediately.
    """
    if not user.is_super_admin or user.account_status != "ACTIVE":
        raise AuthorizationError("Platform operator access required")
    return user


SuperAdmin = Annotated[User, Depends(require_super_admin)]


def require_business_permission(permission: str) -> Callable[..., tuple[Business, Membership]]:
    """Dependency factory: (business, membership) or fail closed.

    Checks: business exists + active, viewer has an ACTIVE membership, and
    the effective permission set includes ``permission``.
    """

    def dep(
        request: Request,
        db: Db,
        user: CurrentUser,
        business_id: Annotated[uuid.UUID, Path()],
    ) -> tuple[Business, Membership]:
        business, membership = require_business_access(db, user=user, business_id=business_id)
        if not has_permission(
            db,
            user_id=user.user_id,
            business_id=business.business_id,
            permission=permission,
        ):
            raise AuthorizationError()
        request.state.business_id = business.business_id
        return business, membership

    return dep
