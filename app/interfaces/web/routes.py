"""Web Panel routes — Jinja2 + HTMX (Phase 9).

This is the server-rendered Web UI per TECHNOLOGY_AND_REPO_SPECIFICATION_V1
section 4 (FastAPI/Jinja2 + HTMX, no Node runtime).

All business operations reuse the same application/use-case layer as the
JSON API (unified permission/use-case layer). Authorization is server-side:
the session cookie identifies the user; business scope is resolved per request
and cross-business access fails closed (404).

Pages:
- /web/login, /web/register, /web/logout
- /web/ (dashboard)
- /web/businesses, /web/businesses/new, /web/businesses/{id}
- /web/businesses/{id}/products, /products/{id}
- /web/businesses/{id}/sources, /sources/{id}
- /web/businesses/{id}/connections, /publications
- /web/businesses/{id}/billing, /members
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import __version__
from app.application import auth as auth_svc
from app.application import business as biz_svc
from app.application import products as products_svc
from app.config.settings import get_settings
from app.domain import enums
from app.domain.errors import AuthenticationError
from app.infrastructure.db import models
from app.infrastructure.db.session import get_session_factory
from app.interfaces.http.deps import SESSION_COOKIE

templates_dir = Path(__file__).parent.parent.parent.parent / "templates"
templates = Jinja2Templates(directory=str(templates_dir))

router = APIRouter(prefix="/web", tags=["web-panel"])


def _get_db() -> Session:
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


DbDep = Annotated[Session, Depends(_get_db)]


def _current_user_from_cookie(
    request: Request,
    db: DbDep,
    session_cookie: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
):
    resolved = auth_svc.resolve_session(db, session_cookie)
    if resolved is None:
        return None
    user, _ = resolved
    request.state.user_id = user.user_id
    return user


def _require_user(
    request: Request,
    db: DbDep,
    session_cookie: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
):
    user = _current_user_from_cookie(request, db, session_cookie)
    if user is None:
        raise AuthenticationError("Not authenticated")
    return user


# --- Auth pages --------------------------------------------------------------


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, db: DbDep):
    user = _current_user_from_cookie(request, db)
    if user:
        return RedirectResponse(url="/web/", status_code=302)
    return templates.TemplateResponse(
        request,
        "login.html",
        {"current_user": None, "version": __version__},
    )


@router.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    db: DbDep,
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
):
    try:
        _user, token = auth_svc.authenticate(
            db,
            email=email.strip(),
            password=password,
            ip=request.client.host if request.client else None,
            user_agent=(request.headers.get("user-agent") or "")[:512]
            or None,
        )
    except Exception:
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "current_user": None,
                "error": "ایمیل یا رمز عبور اشتباه است",
                "version": __version__,
            },
            status_code=401,
        )
    response = RedirectResponse(url="/web/", status_code=302)
    response.set_cookie(
        key=get_settings().session_cookie_name,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=30 * 24 * 3600,
    )
    return response


@router.get("/register", response_class=HTMLResponse)
def register_page(request: Request, db: DbDep):
    user = _current_user_from_cookie(request, db)
    if user:
        return RedirectResponse(url="/web/", status_code=302)
    return templates.TemplateResponse(
        request,
        "register.html",
        {"current_user": None, "version": __version__},
    )


@router.post("/register", response_class=HTMLResponse)
def register_submit(
    request: Request,
    db: DbDep,
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
    display_name: Annotated[str, Form()],
):
    try:
        auth_svc.register_user(
            db,
            email=email.strip(),
            password=password,
            display_name=display_name.strip(),
        )
        db.commit()
        _user, token = auth_svc.authenticate(
            db,
            email=email.strip(),
            password=password,
            ip=request.client.host if request.client else None,
            user_agent=(request.headers.get("user-agent") or "")[:512]
            or None,
        )
    except Exception as exc:
        return templates.TemplateResponse(
            request,
            "register.html",
            {
                "current_user": None,
                "error": str(exc)[:300],
                "version": __version__,
            },
            status_code=400,
        )
    response = RedirectResponse(url="/web/", status_code=302)
    response.set_cookie(
        key=get_settings().session_cookie_name,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=30 * 24 * 3600,
    )
    return response


@router.get("/logout")
def logout(request: Request, db: DbDep):
    # Try to revoke session if present
    try:
        cookie = request.cookies.get(get_settings().session_cookie_name)
        if cookie:
            resolved = auth_svc.resolve_session(db, cookie)
            if resolved is not None:
                _user, session = resolved
                auth_svc.revoke_session(
                    db, session, actor_user_id=_user.user_id
                )
                db.commit()
    except Exception:
        pass
    response = RedirectResponse(url="/web/login", status_code=302)
    response.delete_cookie(key=get_settings().session_cookie_name)
    return response


# --- Dashboard ---------------------------------------------------------------


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: DbDep):
    user = _current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/web/login", status_code=302)
    businesses = biz_svc.list_user_businesses(db, user_id=user.user_id)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "current_user": user,
            "businesses": businesses,
            "version": __version__,
        },
    )


# --- Businesses --------------------------------------------------------------


@router.get("/businesses", response_class=HTMLResponse)
def businesses_list(request: Request, db: DbDep):
    user = _current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/web/login", status_code=302)
    businesses = biz_svc.list_user_businesses(db, user_id=user.user_id)
    btypes = db.scalars(select(models.BusinessType)).all()
    return templates.TemplateResponse(
        request,
        "businesses.html",
        {
            "current_user": user,
            "businesses": businesses,
            "business_types": btypes,
            "version": __version__,
        },
    )


@router.get("/businesses/{business_id}", response_class=HTMLResponse)
def business_detail(request: Request, business_id: uuid.UUID, db: DbDep):
    user = _current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/web/login", status_code=302)
    try:
        business, _membership = biz_svc.require_business_access(
            db, user=user, business_id=business_id
        )
    except Exception:
        return templates.TemplateResponse(
            request,
            "error.html",
            {
                "current_user": user,
                "error": "کسب‌وکار یافت نشد",
                "version": __version__,
            },
            status_code=404,
        )
    return templates.TemplateResponse(
        request,
        "business_detail.html",
        {
            "current_user": user,
            "business": business,
            "version": __version__,
        },
    )


@router.get("/businesses/{business_id}/products", response_class=HTMLResponse)
def products_page(
    request: Request,
    business_id: uuid.UUID,
    db: DbDep,
    q: str | None = None,
    state: str | None = None,
):
    user = _current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/web/login", status_code=302)
    try:
        business, _ = biz_svc.require_business_access(
            db, user=user, business_id=business_id
        )
    except Exception:
        return HTMLResponse("Not found", status_code=404)
    parsed_state = None
    if state:
        try:
            parsed_state = enums.ProductLifecycle(state)
        except ValueError:
            parsed_state = None
    products = products_svc.list_products(
        db, business_id=business_id, state=parsed_state, q=q
    )
    return templates.TemplateResponse(
        request,
        "products.html",
        {
            "current_user": user,
            "business": business,
            "products": products,
            "q": q,
            "state": state,
            "version": __version__,
        },
    )


@router.get(
    "/businesses/{business_id}/products/{product_id}",
    response_class=HTMLResponse,
)
def product_detail_page(
    request: Request,
    business_id: uuid.UUID,
    product_id: uuid.UUID,
    db: DbDep,
):
    user = _current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/web/login", status_code=302)
    try:
        business, _ = biz_svc.require_business_access(
            db, user=user, business_id=business_id
        )
    except Exception:
        return HTMLResponse("Not found", status_code=404)
    product = db.scalar(
        select(models.Product).where(
            models.Product.product_id == product_id,
            models.Product.business_id == business_id,
        )
    )
    if not product:
        return HTMLResponse("Product not found", status_code=404)
    return templates.TemplateResponse(
        request,
        "product_detail.html",
        {
            "current_user": user,
            "business": business,
            "product": product,
            "version": __version__,
        },
    )


# --- HTMX partials -----------------------------------------------------------


@router.get(
    "/businesses/{business_id}/sync-jobs/partial",
    response_class=HTMLResponse,
)
def sync_jobs_partial(request: Request, business_id: uuid.UUID, db: DbDep):
    import html as html_lib

    user = _current_user_from_cookie(request, db)
    if not user:
        return HTMLResponse("", status_code=401)
    try:
        biz_svc.require_business_access(
            db, user=user, business_id=business_id
        )
    except Exception:
        return HTMLResponse("", status_code=404)
    jobs = db.scalars(
        select(models.SyncJob)
        .where(models.SyncJob.business_id == business_id)
        .order_by(models.SyncJob.created_at.desc())
        .limit(10)
    ).all()
    html = "<ul class='sync-list'>"
    for j in jobs:
        status = html_lib.escape(str(j.status))
        trigger = html_lib.escape(str(j.trigger))
        sid = html_lib.escape(str(j.source_id))
        created = html_lib.escape(str(j.created_at))
        html += (
            f"<li><span class='badge'>{status}</span> "
            f"{trigger} — {sid} — {created}</li>"
        )
    if not jobs:
        html += "<li class='muted'>همگام‌سازی اخیری وجود ندارد</li>"
    html += "</ul>"
    return HTMLResponse(html)


@router.get(
    "/businesses/{business_id}/notifications/partial",
    response_class=HTMLResponse,
)
def notifications_partial(
    request: Request, business_id: uuid.UUID, db: DbDep
):
    import html as html_lib

    user = _current_user_from_cookie(request, db)
    if not user:
        return HTMLResponse("", status_code=401)
    try:
        biz_svc.require_business_access(
            db, user=user, business_id=business_id
        )
    except Exception:
        return HTMLResponse("", status_code=404)
    notifs = db.scalars(
        select(models.Notification)
        .where(
            models.Notification.business_id == business_id,
            models.Notification.recipient_user_id == user.user_id,
        )
        .order_by(models.Notification.created_at.desc())
        .limit(10)
    ).all()
    html = "<ul class='notif-list'>"
    for n in notifs:
        title = html_lib.escape(str(n.title))
        body = html_lib.escape(str(n.body[:100]))
        created = html_lib.escape(str(n.created_at))
        html += (
            f"<li><strong>{title}</strong>: {body} "
            f"<span class='muted'>{created}</span></li>"
        )
    if not notifs:
        html += "<li class='muted'>اعلانی وجود ندارد</li>"
    html += "</ul>"
    return HTMLResponse(html)


@router.get(
    "/businesses/{business_id}/publications/partial",
    response_class=HTMLResponse,
)
def publications_partial(
    request: Request,
    business_id: uuid.UUID,
    db: DbDep,
    product_id: uuid.UUID | None = None,
):
    import html as html_lib

    user = _current_user_from_cookie(request, db)
    if not user:
        return HTMLResponse("", status_code=401)
    try:
        biz_svc.require_business_access(
            db, user=user, business_id=business_id
        )
    except Exception:
        return HTMLResponse("", status_code=404)
    stmt = select(models.Publication).where(
        models.Publication.business_id == business_id
    )
    if product_id:
        stmt = stmt.where(models.Publication.product_id == product_id)
    pubs = db.scalars(
        stmt.order_by(models.Publication.created_at.desc()).limit(20)
    ).all()
    html = (
        "<table class='table'><tr><th>وضعیت</th>"
        "<th>اتصال</th><th>پیام</th></tr>"
    )
    for p in pubs:
        status = html_lib.escape(str(p.status))
        conn = html_lib.escape(str(p.connection_id))
        mid = html_lib.escape(str(p.remote_message_id or "—"))
        html += f"<tr><td>{status}</td><td>{conn}</td><td>{mid}</td></tr>"
    if not pubs:
        html += "<tr><td colspan=3 class='muted'>انتشاری وجود ندارد</td></tr>"
    html += "</table>"
    return HTMLResponse(html)
