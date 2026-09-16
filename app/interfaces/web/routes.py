"""Web Panel routes — Jinja2 + HTMX (Phase 9) — robust + full sources flow."""

from __future__ import annotations

import contextlib
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import __version__
from app.application import auth as auth_svc
from app.application import business as biz_svc
from app.application import products as products_svc
from app.application import sources as sources_svc
from app.config.settings import get_settings
from app.domain import enums
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
        with contextlib.suppress(Exception):
            db.commit()
    except Exception:
        with contextlib.suppress(Exception):
            db.rollback()
        raise
    finally:
        with contextlib.suppress(Exception):
            db.close()


DbDep = Annotated[Session, Depends(_get_db)]


def _current_user_from_cookie(request: Request, db: DbDep):
    """Robust cookie reading — avoids fragile Cookie(alias=...) dependency."""
    try:
        token = request.cookies.get(SESSION_COOKIE)
        if not token:
            # fallback to settings name (env may override)
            token = request.cookies.get(get_settings().session_cookie_name)
        if not token:
            return None
        resolved = auth_svc.resolve_session(db, token)
    except Exception:
        with contextlib.suppress(Exception):
            db.rollback()
        return None
    if resolved is None:
        return None
    user, _ = resolved
    request.state.user_id = user.user_id
    return user


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
        db.commit()
    except Exception:
        with contextlib.suppress(Exception):
            db.rollback()
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
    settings = get_settings()
    response = RedirectResponse(url="/web/", status_code=302)
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        httponly=True,
        samesite="lax",
        secure=settings.is_prod,
        path="/",
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
        db.commit()
    except Exception as exc:
        with contextlib.suppress(Exception):
            db.rollback()
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
    settings = get_settings()
    response = RedirectResponse(url="/web/", status_code=302)
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        httponly=True,
        samesite="lax",
        secure=settings.is_prod,
        path="/",
        max_age=30 * 24 * 3600,
    )
    return response


@router.get("/logout")
def logout(request: Request, db: DbDep):
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
        with contextlib.suppress(Exception):
            db.rollback()
    response = RedirectResponse(url="/web/login", status_code=302)
    response.delete_cookie(
        key=get_settings().session_cookie_name, path="/"
    )
    return response


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: DbDep):
    user = _current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/web/login", status_code=302)
    try:
        businesses = biz_svc.list_businesses(db, user=user)
    except Exception:
        businesses = []
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "current_user": user,
            "businesses": businesses,
            "version": __version__,
        },
    )


@router.get("/businesses", response_class=HTMLResponse)
def businesses_list(request: Request, db: DbDep):
    user = _current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/web/login", status_code=302)
    try:
        businesses = biz_svc.list_businesses(db, user=user)
    except Exception:
        businesses = []
    try:
        btypes = db.scalars(select(models.BusinessType)).all()
    except Exception:
        btypes = []
        with contextlib.suppress(Exception):
            db.rollback()
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


@router.get("/businesses/new", response_class=HTMLResponse)
def businesses_new_redirect(request: Request, db: DbDep):
    user = _current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/web/login", status_code=302)
    return RedirectResponse(url="/web/businesses", status_code=302)


@router.post("/businesses", response_class=HTMLResponse)
def businesses_create(
    request: Request,
    db: DbDep,
    name: Annotated[str, Form()],
    business_type_key: Annotated[str, Form()],
):
    user = _current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/web/login", status_code=302)
    try:
        business = biz_svc.create_business(
            db,
            user=user,
            name=name.strip(),
            business_type_key=business_type_key.strip(),
        )
        db.commit()
        return RedirectResponse(
            url=f"/web/businesses/{business.business_id}", status_code=302
        )
    except Exception as exc:
        with contextlib.suppress(Exception):
            db.rollback()
        try:
            businesses = biz_svc.list_businesses(db, user=user)
        except Exception:
            businesses = []
        try:
            btypes = db.scalars(select(models.BusinessType)).all()
        except Exception:
            btypes = []
            with contextlib.suppress(Exception):
                db.rollback()
        return templates.TemplateResponse(
            request,
            "businesses.html",
            {
                "current_user": user,
                "businesses": businesses,
                "business_types": btypes,
                "error": str(exc)[:300],
                "version": __version__,
            },
            status_code=400,
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


@router.get(
    "/businesses/{business_id}/sources", response_class=HTMLResponse
)
def business_sources(request: Request, business_id: uuid.UUID, db: DbDep):
    user = _current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/web/login", status_code=302)
    try:
        business, _ = biz_svc.require_business_access(
            db, user=user, business_id=business_id
        )
    except Exception:
        return HTMLResponse("Not found", status_code=404)

    # Robust: if migrations missing (sync_interval_minutes column), return empty + warning
    sources = []
    warning = None
    try:
        sources = db.scalars(
            select(models.Source)
            .where(models.Source.business_id == business_id)
            .order_by(models.Source.created_at.desc())
        ).all()
    except Exception as exc:
        # ProgrammingError: column does not exist or table missing
        warning = (
            f"خطای دیتابیس: {type(exc).__name__} — "
            f"migrations قدیمی: alembic upgrade head — "
            f"{str(exc)[:200]}"
        )
        with contextlib.suppress(Exception):
            db.rollback()
        sources = []

    return templates.TemplateResponse(
        request,
        "sources.html",
        {
            "current_user": user,
            "business": business,
            "sources": sources,
            "warning": warning,
            "version": __version__,
        },
    )


@router.post(
    "/businesses/{business_id}/sources", response_class=HTMLResponse
)
def business_sources_create(
    request: Request,
    business_id: uuid.UUID,
    db: DbDep,
    name: Annotated[str, Form()],
    kind: Annotated[str, Form()],
    external_ref: Annotated[str, Form()] = "",
    sheet_name: Annotated[str, Form()] = "",
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

    try:
        # Validate kind
        kind_enum = enums.SourceKind(kind.strip())
        # Entitlement check is inside create_source (source_count limit)
        from app.application.audit import set_correlation_id

        corr = getattr(request.state, "correlation_id", uuid.uuid4().hex)
        set_correlation_id(corr)
        source = sources_svc.create_source(
            db,
            business_id=business.business_id,
            actor_id=user.user_id,
            correlation_id=corr,
            name=name.strip(),
            kind=kind_enum,
            external_ref=external_ref.strip() or None,
            sheet_name=sheet_name.strip() or None,
        )
        db.commit()
        return RedirectResponse(
            url=f"/web/businesses/{business_id}/sources/{source.source_id}",
            status_code=302,
        )
    except Exception as exc:
        with contextlib.suppress(Exception):
            db.rollback()
        # Render sources page with error
        try:
            sources = db.scalars(
                select(models.Source)
                .where(models.Source.business_id == business_id)
                .order_by(models.Source.created_at.desc())
            ).all()
        except Exception:
            sources = []
            with contextlib.suppress(Exception):
                db.rollback()
        return templates.TemplateResponse(
            request,
            "sources.html",
            {
                "current_user": user,
                "business": business,
                "sources": sources,
                "error": str(exc)[:400],
                "version": __version__,
            },
            status_code=400,
        )


@router.get(
    "/businesses/{business_id}/sources/{source_id}",
    response_class=HTMLResponse,
)
def source_detail(
    request: Request,
    business_id: uuid.UUID,
    source_id: uuid.UUID,
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

    try:
        source = db.scalar(
            select(models.Source).where(
                models.Source.source_id == source_id,
                models.Source.business_id == business_id,
            )
        )
    except Exception as exc:
        with contextlib.suppress(Exception):
            db.rollback()
        return templates.TemplateResponse(
            request,
            "error.html",
            {
                "current_user": user,
                "error": (
                    f"DB قدیمی: {type(exc).__name__}: {str(exc)[:200]} — "
                    f"alembic upgrade head"
                ),
                "version": __version__,
            },
            status_code=500,
        )

    if not source:
        return HTMLResponse("Source not found", status_code=404)

    # Load mappings and import runs robustly
    mappings = []
    import_runs = []
    try:
        mappings = db.scalars(
            select(models.SourceMapping)
            .where(models.SourceMapping.source_id == source_id)
            .order_by(models.SourceMapping.created_at.desc())
            .limit(20)
        ).all()
    except Exception:
        with contextlib.suppress(Exception):
            db.rollback()
    try:
        import_runs = db.scalars(
            select(models.ImportRun)
            .where(models.ImportRun.source_id == source_id)
            .order_by(models.ImportRun.created_at.desc())
            .limit(20)
        ).all()
    except Exception:
        with contextlib.suppress(Exception):
            db.rollback()

    return templates.TemplateResponse(
        request,
        "source_detail.html",
        {
            "current_user": user,
            "business": business,
            "source": source,
            "mappings": mappings,
            "import_runs": import_runs,
            "version": __version__,
        },
    )


@router.post(
    "/businesses/{business_id}/sources/{source_id}/import",
    response_class=HTMLResponse,
)
async def source_import(
    request: Request,
    business_id: uuid.UUID,
    source_id: uuid.UUID,
    db: DbDep,
    file: Annotated[UploadFile | None, File()] = None,
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

    try:
        source = db.scalar(
            select(models.Source).where(
                models.Source.source_id == source_id,
                models.Source.business_id == business_id,
            )
        )
    except Exception as exc:
        with contextlib.suppress(Exception):
            db.rollback()
        return templates.TemplateResponse(
            request,
            "error.html",
            {
                "current_user": user,
                "error": f"DB error: {exc}",
                "version": __version__,
            },
            status_code=500,
        )

    if not source:
        return HTMLResponse("Source not found", status_code=404)

    file_bytes = None
    if file is not None:
        file_bytes = await file.read()

    try:
        from app.application import import_pipeline
        from app.application.audit import set_correlation_id

        corr = getattr(request.state, "correlation_id", uuid.uuid4().hex)
        set_correlation_id(corr)
        # Check no running import
        running = db.scalar(
            select(models.ImportRun).where(
                models.ImportRun.source_id == source.source_id,
                models.ImportRun.status == enums.ImportRunStatus.RUNNING.value,
            )
        )
        if running:
            raise ValueError("یک import در حال اجرا است")

        import_pipeline.run_import(
            db,
            source=source,
            business_id=business.business_id,
            actor_id=user.user_id,
            correlation_id=corr,
            file_bytes=file_bytes,
        )
        db.commit()
        # Redirect to source detail with success message
        return RedirectResponse(
            url=f"/web/businesses/{business_id}/sources/{source_id}",
            status_code=302,
        )
    except Exception as exc:
        with contextlib.suppress(Exception):
            db.rollback()
        # Render source detail with error
        try:
            mappings = db.scalars(
                select(models.SourceMapping)
                .where(models.SourceMapping.source_id == source_id)
                .order_by(models.SourceMapping.created_at.desc())
                .limit(20)
            ).all()
        except Exception:
            mappings = []
            with contextlib.suppress(Exception):
                db.rollback()
        try:
            import_runs = db.scalars(
                select(models.ImportRun)
                .where(models.ImportRun.source_id == source_id)
                .order_by(models.ImportRun.created_at.desc())
                .limit(20)
            ).all()
        except Exception:
            import_runs = []
            with contextlib.suppress(Exception):
                db.rollback()

        return templates.TemplateResponse(
            request,
            "source_detail.html",
            {
                "current_user": user,
                "business": business,
                "source": source,
                "mappings": mappings,
                "import_runs": import_runs,
                "error": str(exc)[:500],
                "version": __version__,
            },
            status_code=400,
        )


@router.get(
    "/businesses/{business_id}/connections", response_class=HTMLResponse
)
def business_connections(request: Request, business_id: uuid.UUID, db: DbDep):
    user = _current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/web/login", status_code=302)
    try:
        business, _ = biz_svc.require_business_access(
            db, user=user, business_id=business_id
        )
    except Exception:
        return HTMLResponse("Not found", status_code=404)
    try:
        connections = db.scalars(
            select(models.PlatformConnection).where(
                models.PlatformConnection.business_id == business_id
            )
        ).all()
    except Exception:
        connections = []
        with contextlib.suppress(Exception):
            db.rollback()
    return templates.TemplateResponse(
        request,
        "business_detail.html",
        {
            "current_user": user,
            "business": business,
            "connections": connections,
            "extra_section": "connections",
            "version": __version__,
        },
    )


@router.get(
    "/businesses/{business_id}/billing", response_class=HTMLResponse
)
@router.get(
    "/businesses/{business_id}/members", response_class=HTMLResponse
)
@router.get(
    "/businesses/{business_id}/publications", response_class=HTMLResponse
)
def business_generic_tab(request: Request, business_id: uuid.UUID, db: DbDep):
    user = _current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/web/login", status_code=302)
    try:
        business, _ = biz_svc.require_business_access(
            db, user=user, business_id=business_id
        )
    except Exception:
        return HTMLResponse("Not found", status_code=404)
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
    try:
        products = products_svc.list_products(
            db, business_id=business_id, state=parsed_state, q=q
        )
    except Exception:
        products = []
        with contextlib.suppress(Exception):
            db.rollback()
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
    try:
        product = db.scalar(
            select(models.Product).where(
                models.Product.product_id == product_id,
                models.Product.business_id == business_id,
            )
        )
    except Exception:
        with contextlib.suppress(Exception):
            db.rollback()
        return HTMLResponse("DB error - run migrations", status_code=500)
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
    try:
        jobs = db.scalars(
            select(models.SyncJob)
            .where(models.SyncJob.business_id == business_id)
            .order_by(models.SyncJob.created_at.desc())
            .limit(10)
        ).all()
    except Exception:
        # Table missing or connection lost - return graceful empty
        with contextlib.suppress(Exception):
            db.rollback()
        return HTMLResponse(
            "<ul class='sync-list'>"
            "<li class='muted'>همگام‌سازی (DB خطا)</li></ul>"
        )
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
    try:
        notifs = db.scalars(
            select(models.Notification)
            .where(
                models.Notification.business_id == business_id,
                models.Notification.recipient_user_id == user.user_id,
            )
            .order_by(models.Notification.created_at.desc())
            .limit(10)
        ).all()
    except Exception:
        with contextlib.suppress(Exception):
            db.rollback()
        # Graceful fallback when notifications table missing (old migration)
        return HTMLResponse(
            "<ul class='notif-list'>"
            "<li class='muted'>اعلان‌ها (نیاز به migration)</li></ul>"
        )
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
    try:
        stmt = select(models.Publication).where(
            models.Publication.business_id == business_id
        )
        if product_id:
            stmt = stmt.where(models.Publication.product_id == product_id)
        pubs = db.scalars(
            stmt.order_by(models.Publication.created_at.desc()).limit(20)
        ).all()
    except Exception:
        with contextlib.suppress(Exception):
            db.rollback()
        return HTMLResponse(
            "<table class='table'><tr><td class='muted'>انتشارها (DB خطا)</td></tr></table>"
        )
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
