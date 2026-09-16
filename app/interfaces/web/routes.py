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
from app.application import mapping as mapping_svc
from app.application import products as products_svc
from app.application import sources as sources_svc
from app.config.settings import get_settings
from app.domain import enums
from app.infrastructure.db import models
from app.infrastructure.db.session import get_session_factory
from app.infrastructure.sources import read_source
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
        # Prevent duplicate creation on double-click: if same name exists, redirect to existing
        try:
            existing_list = biz_svc.list_businesses(db, user=user)
            for eb in existing_list:
                if eb.business_name.strip().lower() == name.strip().lower():
                    return RedirectResponse(
                        url=f"/web/businesses/{eb.business_id}", status_code=302
                    )
        except Exception:
            with contextlib.suppress(Exception):
                db.rollback()

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
            .where(
                models.Source.business_id == business_id,
                models.Source.status != enums.SourceStatus.ARCHIVED.value,
            )
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

    # Check active mapping for UI hints
    active_mapping = None
    try:
        active_mapping = db.scalar(
            select(models.SourceMapping).where(
                models.SourceMapping.source_id == source_id,
                models.SourceMapping.status == enums.MappingStatus.ACTIVE.value,
            )
        )
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
            "active_mapping": active_mapping,
            "version": __version__,
        },
    )


@router.post(
    "/businesses/{business_id}/sources/{source_id}/auto-setup",
    response_class=HTMLResponse,
)
async def source_auto_setup(
    request: Request,
    business_id: uuid.UUID,
    source_id: uuid.UUID,
    db: DbDep,
    file: Annotated[UploadFile | None, File()] = None,
):
    """One-click auto setup: read -> suggest -> create DRAFT -> activate -> preview.

    Customer-friendly: no manual mapping needed if heuristic finds 'name'.
    If name not found, still creates draft and shows editor for quick fix.
    """
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
            {"current_user": user, "error": f"DB error: {exc}", "version": __version__},
            status_code=500,
        )
    if not source:
        return HTMLResponse("Source not found", status_code=404)

    file_bytes = None
    if file is not None:
        file_bytes = await file.read()
        if len(file_bytes) == 0:
            file_bytes = None

    # Read source
    try:
        src_read = read_source(source, file_bytes=file_bytes)
    except Exception as exc:
        src_read = None
        read_error = str(exc)[:400]
    else:
        read_error = src_read.error if src_read and src_read.error else None

    if src_read is None or (not src_read.headers and not src_read.complete):
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
        # Friendly error with guidance
        friendly = read_error or "headers خالی"
        if "public fetch failed" in friendly or "no credentials" in friendly:
            friendly = (
                "شیت خوانده نشد. برای Google Sheets:\n"
                "1) لینک را کامل Paste کنید (مثل https://docs.google.com/spreadsheets/d/XXXX/edit)\n"
                "2) در Google Sheets، دکمه Share → General access → Anyone with the link → Viewer\n"
                "3) دوباره امتحان کنید. اگر می‌خواهید خصوصی بماند، باید از طریق پنل ادمین credentials_ref تنظیم شود.\n"
                f"جزئیات: {read_error}"
            )
        return templates.TemplateResponse(
            request,
            "source_detail.html",
            {
                "current_user": user,
                "business": business,
                "source": source,
                "mappings": mappings,
                "import_runs": import_runs,
                "active_mapping": None,
                "error": friendly,
                "version": __version__,
            },
            status_code=400,
        )

    suggested_entries = mapping_svc.suggest_entries(src_read.headers)
    suggested_records = mapping_svc.entries_to_records(suggested_entries)

    # Auto-create mapping if we have at least a name
    has_name = any(r.get("canonical_field") == "name" for r in suggested_records)
    if not has_name and suggested_records:
        # Try to force first column as name if nothing matched (customer-friendly fallback)
        # Only if first column looks like product name (not ID etc)
        first = suggested_records[0]
        if first.get("column"):
            first["canonical_field"] = "name"
            first["field_kind"] = "CORE"
            first["field_type"] = "STRING"
            first["required"] = True
            first["display_name"] = "نام محصول"
            has_name = True

    if not has_name:
        # Can't auto-activate, show editor with friendly message
        return templates.TemplateResponse(
            request,
            "mapping_editor.html",
            {
                "current_user": user,
                "business": business,
                "source": source,
                "headers": src_read.headers,
                "suggested": suggested_records,
                "complete": src_read.complete,
                "read_error": None,
                "error": "سیستم نتوانست ستون نام محصول را خودکار تشخیص دهد. لطفاً در جدول زیر، یک ستون را به 'نام محصول (name)' نگاشت کنید و ذخیره کنید — بقیه خودکار فعال می‌شود.",
                "version": __version__,
            },
        )

    # Create and activate mapping automatically
    try:
        # Deduplicate canonical fields
        seen = set()
        cleaned = []
        for e in suggested_records:
            col = (e.get("column") or "").strip()
            if not col:
                continue
            canon = (e.get("canonical_field") or "").strip() or None
            kind = (e.get("field_kind") or "CUSTOM").strip()
            if not canon:
                kind = "CUSTOM"
            if canon and canon in seen:
                kind = "CUSTOM"
                canon = None
            if canon:
                seen.add(canon)
            cleaned.append(
                {
                    "column": col,
                    "canonical_field": canon,
                    "field_kind": kind,
                    "field_type": e.get("field_type") or "STRING",
                    "display_name": (e.get("display_name") or col).strip(),
                    "required": bool(e.get("required")),
                    "template_exposed": False,
                    "confidence": float(e.get("confidence") or 0.9),
                    "evidence": e.get("evidence") or "auto-setup heuristic",
                }
            )

        mapping = mapping_svc.create_mapping_version(
            db, source=source, entries=cleaned, actor_id=user.user_id
        )
        mapping_svc.activate_mapping(
            db, source=source, mapping=mapping, actor_id=user.user_id
        )
        db.commit()
    except Exception as exc:
        with contextlib.suppress(Exception):
            db.rollback()
        return templates.TemplateResponse(
            request,
            "mapping_editor.html",
            {
                "current_user": user,
                "business": business,
                "source": source,
                "headers": src_read.headers,
                "suggested": suggested_records,
                "error": f"راه‌اندازی خودکار ناموفق: {exc} — لطفاً دستی ویرایش کنید.",
                "version": __version__,
            },
            status_code=400,
        )

    # After auto activation, go to preview (dry-run) automatically
    try:
        from app.application import import_pipeline
        from app.application.audit import set_correlation_id

        corr = getattr(request.state, "correlation_id", uuid.uuid4().hex)
        set_correlation_id(corr)
        preview = import_pipeline.preview_import(
            db,
            source=source,
            business_id=business.business_id,
            file_bytes=file_bytes,
        )
    except Exception as exc:
        with contextlib.suppress(Exception):
            db.rollback()
        preview = {
            "error": str(exc)[:500],
            "complete": False,
            "headers": src_read.headers,
            "rows": [],
            "counts": {},
        }

    return templates.TemplateResponse(
        request,
        "preview.html",
        {
            "current_user": user,
            "business": business,
            "source": source,
            "preview": preview,
            "auto_setup_success": True,
            "version": __version__,
        },
    )


@router.post(
    "/businesses/{business_id}/sources/{source_id}/auto-import",
    response_class=HTMLResponse,
)
async def source_auto_import(
    request: Request,
    business_id: uuid.UUID,
    source_id: uuid.UUID,
    db: DbDep,
    file: Annotated[UploadFile | None, File()] = None,
):
    """Ultimate one-click: auto-setup (if needed) + import.

    If active mapping exists, just imports. If not, does auto-setup then import.
    """
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
            {"current_user": user, "error": f"DB error: {exc}", "version": __version__},
            status_code=500,
        )
    if not source:
        return HTMLResponse("Source not found", status_code=404)

    file_bytes = None
    if file is not None:
        file_bytes = await file.read()
        if len(file_bytes) == 0:
            file_bytes = None

    # Ensure active mapping exists, if not auto-setup
    try:
        active = db.scalar(
            select(models.SourceMapping).where(
                models.SourceMapping.source_id == source_id,
                models.SourceMapping.status == enums.MappingStatus.ACTIVE.value,
            )
        )
    except Exception:
        active = None
        with contextlib.suppress(Exception):
            db.rollback()

    if not active:
        # Call auto-setup logic inline (reuse code)
        try:
            src_read = read_source(source, file_bytes=file_bytes)
        except Exception:
            src_read = None

        if src_read and src_read.headers:
            suggested_entries = mapping_svc.suggest_entries(src_read.headers)
            suggested_records = mapping_svc.entries_to_records(suggested_entries)
            has_name = any(r.get("canonical_field") == "name" for r in suggested_records)
            if not has_name and suggested_records:
                first = suggested_records[0]
                first["canonical_field"] = "name"
                first["field_kind"] = "CORE"
                first["required"] = True

            if has_name or (suggested_records and suggested_records[0].get("canonical_field") == "name"):
                try:
                    seen = set()
                    cleaned = []
                    for e in suggested_records:
                        col = (e.get("column") or "").strip()
                        if not col:
                            continue
                        canon = (e.get("canonical_field") or "").strip() or None
                        kind = (e.get("field_kind") or "CUSTOM").strip()
                        if not canon:
                            kind = "CUSTOM"
                        if canon and canon in seen:
                            kind = "CUSTOM"
                            canon = None
                        if canon:
                            seen.add(canon)
                        cleaned.append(
                            {
                                "column": col,
                                "canonical_field": canon,
                                "field_kind": kind,
                                "field_type": e.get("field_type") or "STRING",
                                "display_name": (e.get("display_name") or col).strip(),
                                "required": bool(e.get("required")),
                                "template_exposed": False,
                                "confidence": float(e.get("confidence") or 0.9),
                                "evidence": "auto-import heuristic",
                            }
                        )
                    mapping = mapping_svc.create_mapping_version(
                        db, source=source, entries=cleaned, actor_id=user.user_id
                    )
                    mapping_svc.activate_mapping(
                        db, source=source, mapping=mapping, actor_id=user.user_id
                    )
                    db.commit()
                except Exception:
                    with contextlib.suppress(Exception):
                        db.rollback()
                    # Continue to try import anyway (will fail with no mapping error, shown nicely)

    # Now run import
    try:
        from app.application import import_pipeline
        from app.application.audit import set_correlation_id

        corr = getattr(request.state, "correlation_id", uuid.uuid4().hex)
        set_correlation_id(corr)

        running = db.scalar(
            select(models.ImportRun).where(
                models.ImportRun.source_id == source.source_id,
                models.ImportRun.status == enums.ImportRunStatus.RUNNING.value,
            )
        )
        if running:
            raise ValueError("یک import در حال اجرا است — لطفاً چند ثانیه صبر کنید")

        outcome = import_pipeline.run_import(
            db,
            source=source,
            business_id=business.business_id,
            actor_id=user.user_id,
            correlation_id=corr,
            file_bytes=file_bytes,
        )
        db.commit()
        # Show success with counts
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

        # Render source detail with success message
        return templates.TemplateResponse(
            request,
            "source_detail.html",
            {
                "current_user": user,
                "business": business,
                "source": source,
                "mappings": mappings,
                "import_runs": import_runs,
                "active_mapping": db.scalar(
                    select(models.SourceMapping).where(
                        models.SourceMapping.source_id == source_id,
                        models.SourceMapping.status == enums.MappingStatus.ACTIVE.value,
                    )
                )
                if True
                else None,
                "success": f"✅ Import موفق! {outcome.counts.get('new',0)} جدید، {outcome.counts.get('changed',0)} تغییر، {outcome.counts.get('unchanged',0)} بدون تغییر، {outcome.counts.get('invalid',0)} نامعتبر، {outcome.counts.get('blank',0)} خالی — جزئیات در Import Runs. نکته: اگر قبلاً محصول بدون قیمت بوده و الان قیمت‌دار شده، به عنوان 'تغییر' حساب می‌شود (درست است).",
                "version": __version__,
            },
        )
    except Exception as exc:
        with contextlib.suppress(Exception):
            db.rollback()
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
                "active_mapping": None,
                "error": str(exc)[:600],
                "version": __version__,
            },
            status_code=400,
        )


@router.post(
    "/businesses/{business_id}/sources/{source_id}/mapping/suggest",
    response_class=HTMLResponse,
)
async def source_mapping_suggest(
    request: Request,
    business_id: uuid.UUID,
    source_id: uuid.UUID,
    db: DbDep,
    file: Annotated[UploadFile | None, File()] = None,
):
    """Suggest mapping: read headers from source, heuristic suggestion, render editor.

    Flow per SOURCE_SYNC spec sections 5-7:
    discovery -> suggestion -> correction -> validation -> preview -> activate.
    """
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
        if len(file_bytes) == 0:
            file_bytes = None

    # Read source headers
    try:
        src_read = read_source(source, file_bytes=file_bytes)
    except Exception as exc:
        src_read = None
        read_error = str(exc)[:300]
    else:
        read_error = src_read.error if src_read and src_read.error else None

    if src_read is None or (not src_read.headers and not src_read.complete):
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
                "error": f"خواندن منبع ناموفق: {read_error or 'headers خالی'} — برای Excel فایل را آپلود کنید، برای Google Sheets دسترسی و credentials را بررسی کنید.",
                "version": __version__,
            },
            status_code=400,
        )

    suggested_entries = mapping_svc.suggest_entries(src_read.headers)
    suggested_records = mapping_svc.entries_to_records(suggested_entries)

    # Load existing active mapping to pre-fill editor (customer-friendly: keep previous active state)
    existing_active = None
    try:
        existing_active = db.scalar(
            select(models.SourceMapping).where(
                models.SourceMapping.source_id == source_id,
                models.SourceMapping.status == enums.MappingStatus.ACTIVE.value,
            )
        )
    except Exception:
        with contextlib.suppress(Exception):
            db.rollback()

    # If active mapping exists, merge it with suggestions to keep user's previous choices
    if existing_active and existing_active.entries:
        # Build lookup of existing mapping by column
        existing_by_col = {e.get("column"): e for e in existing_active.entries}
        merged = []
        for rec in suggested_records:
            col = rec.get("column")
            if col in existing_by_col:
                # Keep existing mapping for this column (user's previous choice)
                existing = existing_by_col[col]
                merged.append(
                    {
                        "column": col,
                        "canonical_field": existing.get("canonical_field"),
                        "field_kind": existing.get("field_kind", "CUSTOM"),
                        "field_type": existing.get("field_type", "STRING"),
                        "display_name": existing.get("display_name", col),
                        "required": existing.get("required", False),
                        "template_exposed": existing.get("template_exposed", False),
                        "confidence": 1.0,
                        "evidence": f"existing active mapping v{existing_active.version}",
                    }
                )
            else:
                merged.append(rec)
        # Also add any columns from existing that are not in current headers? No, skip
        suggested_records = merged

    return templates.TemplateResponse(
        request,
        "mapping_editor.html",
        {
            "current_user": user,
            "business": business,
            "source": source,
            "headers": src_read.headers,
            "suggested": suggested_records,
            "complete": src_read.complete,
            "read_error": read_error,
            "existing_mapping": existing_active,
            "version": __version__,
        },
    )


@router.post(
    "/businesses/{business_id}/sources/{source_id}/mapping",
    response_class=HTMLResponse,
)
async def source_mapping_create(
    request: Request,
    business_id: uuid.UUID,
    source_id: uuid.UUID,
    db: DbDep,
):
    """Create a new mapping version from edited suggestions (DRAFT).

    Accepts entries_json hidden field containing list[dict] with:
    column, canonical_field, field_kind, field_type, display_name, required, etc.
    """
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

    form = await request.form()
    entries_json = form.get("entries_json") or "[]"
    import json

    try:
        entries = json.loads(entries_json)
    except Exception as exc:
        return templates.TemplateResponse(
            request,
            "mapping_editor.html",
            {
                "current_user": user,
                "business": business,
                "source": source,
                "headers": [],
                "suggested": [],
                "error": f"فرمت نگاشت نامعتبر: {exc}",
                "version": __version__,
            },
            status_code=400,
        )

    # Basic validation: must have at least one CORE name
    if not isinstance(entries, list) or len(entries) == 0:
        return templates.TemplateResponse(
            request,
            "mapping_editor.html",
            {
                "current_user": user,
                "business": business,
                "source": source,
                "headers": [e.get("column", "") for e in entries] if isinstance(entries, list) else [],
                "suggested": entries if isinstance(entries, list) else [],
                "error": "نگاشت خالی است — حداقل یک ستون را نگاشت کنید.",
                "version": __version__,
            },
            status_code=400,
        )

    has_name = any(
        (e.get("canonical_field") == "name" and e.get("field_kind") == "CORE")
        for e in entries
    )
    if not has_name:
        return templates.TemplateResponse(
            request,
            "mapping_editor.html",
            {
                "current_user": user,
                "business": business,
                "source": source,
                "headers": [e.get("column", "") for e in entries],
                "suggested": entries,
                "error": "حداقل یک ستون باید به فیلد name (نام محصول) نگاشت شود.",
                "version": __version__,
            },
            status_code=400,
        )

    # Deduplicate canonical fields: keep first occurrence, turn duplicates into CUSTOM
    seen = set()
    cleaned = []
    for e in entries:
        col = (e.get("column") or "").strip()
        if not col:
            continue
        canon = (e.get("canonical_field") or "").strip() or None
        kind = (e.get("field_kind") or "CUSTOM").strip()
        if not canon:
            kind = "CUSTOM"
        if canon and canon in seen:
            # duplicate -> CUSTOM
            kind = "CUSTOM"
            canon = None
        if canon:
            seen.add(canon)
        cleaned.append(
            {
                "column": col,
                "canonical_field": canon,
                "field_kind": kind,
                "field_type": e.get("field_type") or "STRING",
                "display_name": (e.get("display_name") or col).strip(),
                "required": bool(e.get("required")),
                "template_exposed": bool(e.get("template_exposed", False)),
                "confidence": float(e.get("confidence") or 0.9),
                "evidence": e.get("evidence") or "manual correction",
            }
        )

    try:
        mapping = mapping_svc.create_mapping_version(
            db, source=source, entries=cleaned, actor_id=user.user_id
        )
        # Customer-friendly: auto-activate the new mapping immediately
        # (no need for separate DRAFT->ACTIVE step for simple cases)
        mapping_svc.activate_mapping(
            db, source=source, mapping=mapping, actor_id=user.user_id
        )
        db.commit()
    except Exception as exc:
        with contextlib.suppress(Exception):
            db.rollback()
        return templates.TemplateResponse(
            request,
            "mapping_editor.html",
            {
                "current_user": user,
                "business": business,
                "source": source,
                "headers": [e.get("column", "") for e in cleaned],
                "suggested": cleaned,
                "error": f"ذخیره نگاشت ناموفق: {exc}",
                "version": __version__,
            },
            status_code=400,
        )

    # After creation+activation, redirect to source detail with success
    # Use 302 to show updated active mapping and allow immediate import
    return RedirectResponse(
        url=f"/web/businesses/{business_id}/sources/{source_id}",
        status_code=302,
    )


@router.post(
    "/businesses/{business_id}/sources/{source_id}/mappings/{mapping_id}/activate",
    response_class=HTMLResponse,
)
def source_mapping_activate(
    request: Request,
    business_id: uuid.UUID,
    source_id: uuid.UUID,
    mapping_id: uuid.UUID,
    db: DbDep,
):
    """Activate a DRAFT mapping version -> ACTIVE, supersede old active."""
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
        mapping = db.scalar(
            select(models.SourceMapping).where(
                models.SourceMapping.mapping_id == mapping_id,
                models.SourceMapping.source_id == source_id,
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

    if not source or not mapping:
        return HTMLResponse("Mapping or Source not found", status_code=404)

    try:
        mapping_svc.activate_mapping(
            db, source=source, mapping=mapping, actor_id=user.user_id
        )
        db.commit()
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
                "error": f"فعال‌سازی نگاشت ناموفق: {exc}",
                "version": __version__,
            },
            status_code=400,
        )

    return RedirectResponse(
        url=f"/web/businesses/{business_id}/sources/{source_id}",
        status_code=302,
    )


@router.post(
    "/businesses/{business_id}/sources/{source_id}/preview",
    response_class=HTMLResponse,
)
async def source_preview(
    request: Request,
    business_id: uuid.UUID,
    source_id: uuid.UUID,
    db: DbDep,
    file: Annotated[UploadFile | None, File()] = None,
):
    """Dry-run preview: same extraction/validation/identity logic, zero writes.

    Renders preview.html with counts and row-level diff.
    """
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

        preview = import_pipeline.preview_import(
            db,
            source=source,
            business_id=business.business_id,
            file_bytes=file_bytes,
        )
    except Exception as exc:
        with contextlib.suppress(Exception):
            db.rollback()
        preview = {
            "error": str(exc)[:500],
            "complete": False,
            "headers": [],
            "rows": [],
            "counts": {},
        }

    return templates.TemplateResponse(
        request,
        "preview.html",
        {
            "current_user": user,
            "business": business,
            "source": source,
            "preview": preview,
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


@router.post(
    "/businesses/{business_id}/sources/{source_id}/delete",
    response_class=HTMLResponse,
)
def source_delete(
    request: Request,
    business_id: uuid.UUID,
    source_id: uuid.UUID,
    db: DbDep,
):
    """Delete/archive a source. Hard delete if no import runs, otherwise soft archive."""
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
            {"current_user": user, "error": f"DB error: {exc}", "version": __version__},
            status_code=500,
        )
    if not source:
        return HTMLResponse("Source not found", status_code=404)

    try:
        # Check if source has import runs or products linked
        has_runs = db.scalar(
            select(models.ImportRun).where(models.ImportRun.source_id == source_id).limit(1)
        )
        if has_runs:
            # Soft delete: archive
            source.status = enums.SourceStatus.ARCHIVED.value
            db.flush()
            from app.application.audit import AuditService

            AuditService(db).record(
                action="source.archived",
                actor_user_id=user.user_id,
                business_id=business_id,
                target_type="source",
                target_id=str(source.source_id),
                meta={"name": source.name},
            )
        else:
            # Hard delete: no history, safe to remove
            # Delete mappings first
            for m in db.scalars(
                select(models.SourceMapping).where(models.SourceMapping.source_id == source_id)
            ):
                db.delete(m)
            for r in db.scalars(
                select(models.SourceRecord).where(models.SourceRecord.source_id == source_id)
            ):
                db.delete(r)
            db.delete(source)
        db.commit()
    except Exception as exc:
        with contextlib.suppress(Exception):
            db.rollback()
        return templates.TemplateResponse(
            request,
            "error.html",
            {"current_user": user, "error": f"Delete failed: {exc}", "version": __version__},
            status_code=500,
        )

    return RedirectResponse(
        url=f"/web/businesses/{business_id}/sources", status_code=302
    )


@router.get(
    "/businesses/{business_id}/products/{product_id}/edit",
    response_class=HTMLResponse,
)
def product_edit_page(
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
        return HTMLResponse("DB error", status_code=500)
    if not product:
        return HTMLResponse("Product not found", status_code=404)

    return templates.TemplateResponse(
        request,
        "product_edit.html",
        {
            "current_user": user,
            "business": business,
            "product": product,
            "version": __version__,
        },
    )


@router.post(
    "/businesses/{business_id}/products/{product_id}/edit",
    response_class=HTMLResponse,
)
def product_edit_submit(
    request: Request,
    business_id: uuid.UUID,
    product_id: uuid.UUID,
    db: DbDep,
    name: Annotated[str, Form()],
    description: Annotated[str, Form()] = "",
):
    """Edit product - only description is manually editable, source is authoritative for price/stock.

    Per user feedback: priority is customer's sheet to reduce hassle.
    Manual edit should be only for description and AI outputs, not price/stock
    which would be overwritten by next auto-sync.
    """
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
        return HTMLResponse("DB error", status_code=500)
    if not product:
        return HTMLResponse("Product not found", status_code=404)

    try:
        # Only description is manually editable - source remains authoritative for price/stock/category/etc
        # This prevents customer confusion: manual price edit would be overwritten by next sheet sync
        product.description = description.strip() or None

        from datetime import UTC, datetime

        product.updated_at = datetime.now(UTC)

        # Create version record for manual edit
        from app.domain import enums as domain_enums

        db.add(
            models.ProductVersion(
                product_id=product.product_id,
                business_id=business_id,
                version_no=product.current_version + 1,
                change_categories=["MANUAL_EDIT_DESCRIPTION"],
                risk_level=domain_enums.ChangeRisk.LOW.value,
                changed_fields={"description": {"manual_edit": True}},
                content_hash="manual_description_edit",
                trigger=domain_enums.SyncTrigger.MANUAL.value,
            )
        )
        product.current_version += 1

        db.commit()
    except Exception as exc:
        with contextlib.suppress(Exception):
            db.rollback()
        return templates.TemplateResponse(
            request,
            "product_edit.html",
            {
                "current_user": user,
                "business": business,
                "product": product,
                "error": f"Save failed: {exc}",
                "version": __version__,
            },
            status_code=400,
        )

    return RedirectResponse(
        url=f"/web/businesses/{business_id}/products/{product_id}", status_code=302
    )


@router.post(
    "/businesses/{business_id}/delete",
    response_class=HTMLResponse,
)
def business_delete(
    request: Request,
    business_id: uuid.UUID,
    db: DbDep,
):
    """Delete a business (owner only) — hard delete with all products/sources."""
    user = _current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/web/login", status_code=302)
    try:
        biz_svc.delete_business(db, user=user, business_id=business_id)
        db.commit()
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
                "error": f"حذف ناموفق: {exc}",
                "version": __version__,
            },
            status_code=400,
        )
    return RedirectResponse(url="/web/businesses", status_code=302)


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
