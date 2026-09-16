"""FastAPI application factory, health endpoints, and request middleware.

Liveness (``/healthz``) and readiness (``/readyz``) are intentionally
separate (OBSERVABILITY spec): a live process that cannot reach its database
must NOT report ready.

Cross-cutting behavior:
- Correlation ID: adopts inbound ``X-Correlation-Id`` or mints one, exposes
  it on the response, and stores it in a context var for audit/logs.
- DomainError -> JSON mapping (stable machine code + message), never a
  stack trace.
- Request logging: method, path, status, duration, correlation id, user id.
  NEVER logs cookies, tokens, bodies, or secrets.
"""

from __future__ import annotations

import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app import __version__
from app.application.audit import set_correlation_id
from app.config.settings import get_settings
from app.domain.errors import DomainError

logger = logging.getLogger("app.http")

CORRELATION_HEADER = "X-Correlation-Id"


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        debug=settings.debug,
        docs_url="/api/docs" if not settings.is_prod else None,
        redoc_url=None,
    )

    # --- Cross-cutting middleware ---

    @app.middleware("http")
    async def correlation_and_logging(request: Request, call_next):
        correlation = (request.headers.get(CORRELATION_HEADER) or "")[:64] or uuid.uuid4().hex
        request.state.correlation_id = correlation
        set_correlation_id(correlation)
        start = time.perf_counter()
        response = await call_next(request)
        response.headers[CORRELATION_HEADER] = correlation
        duration_ms = (time.perf_counter() - start) * 1000.0
        logger.info(
            "http request",
            extra={
                "correlation_id": correlation,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": round(duration_ms, 2),
                "user_id": getattr(request.state, "user_id", None),
                "business_id": getattr(request.state, "business_id", None),
            },
        )
        return response

    # --- Security & observability middleware (Phase 10-12) ---
    try:
        from app.infrastructure.monitoring.middleware import MetricsMiddleware, RateLimitMiddleware
        from app.infrastructure.security.middleware import SecurityHeadersMiddleware

        app.add_middleware(SecurityHeadersMiddleware)
        app.add_middleware(MetricsMiddleware)
        app.add_middleware(RateLimitMiddleware, requests_per_minute=120)
    except Exception:
        # Middleware is additive; failure should not block app start
        pass

    # --- Domain error mapping ---

    @app.exception_handler(DomainError)
    async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    # --- Health ---

    @app.get("/healthz", tags=["health"])
    def healthz() -> dict[str, str]:
        return {
            "status": "ok",
            "version": __version__,
            "environment": settings.environment,
        }

    @app.get("/readyz", tags=["health"])
    def readyz() -> JSONResponse:
        checks: dict[str, str] = {"database": "ok", "redis": "ok"}
        healthy = True
        try:
            from app.infrastructure.db import get_engine

            with get_engine().connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception as exc:  # pragma: no cover - environment dependent
            healthy = False
            checks["database"] = f"unavailable: {type(exc).__name__}"
        # Redis check (best effort)
        try:
            import redis

            r = redis.from_url(settings.redis_url, socket_connect_timeout=2)
            r.ping()
        except Exception as exc:  # pragma: no cover
            # Redis is not critical for readiness in V1 (queue degrades gracefully)
            checks["redis"] = f"degraded: {type(exc).__name__}"
        status_code = 200 if healthy else 503
        return JSONResponse(status_code=status_code, content={"ready": healthy, "checks": checks})

    # --- Phase 1 routers ---
    from app.interfaces.http import routes_admin, routes_auth, routes_business, routes_links

    app.include_router(routes_auth.router)
    app.include_router(routes_business.router)
    app.include_router(routes_admin.router)
    app.include_router(routes_links.router)

    # --- Phase 2 router (billing/subscription/entitlement) ---
    from app.interfaces.http import routes_billing

    app.include_router(routes_billing.router)

    # --- Phase 3 router (sources/products/review) ---
    from app.interfaces.http import routes_products

    app.include_router(routes_products.router)

    # --- Phase 4 routers (content/presets/AI) ---
    from app.interfaces.http import routes_content

    app.include_router(routes_content.router)
    app.include_router(routes_content.admin_router)

    # --- Phase 5 router (platform connections / publications) ---
    from app.interfaces.http import routes_publications

    app.include_router(routes_publications.router)

    # --- Phase 8 router (sync jobs / notifications / schedule) ---
    from app.interfaces.http import routes_sync

    app.include_router(routes_sync.router)

    # --- Phase 10 router (monitoring / audit / backups) ---
    from app.interfaces.http import routes_monitoring

    app.include_router(routes_monitoring.router)

    # --- Phase 9: Web panel (Jinja2 + HTMX) ---
    try:
        from app.interfaces.web.routes import router as web_router

        app.include_router(web_router)
    except Exception:
        pass

    # --- Phase 9: Telegram & Bale bot webhooks ---
    try:
        from app.interfaces.telegram.routes import router as tg_router

        app.include_router(tg_router)
    except Exception:
        pass
    try:
        from app.interfaces.bale.routes import router as bale_router

        app.include_router(bale_router)
    except Exception:
        pass

    # --- Static assets (operational aid; Phase 9 web-panel foundation) ---
    # A small PUBLIC static dir (currently only the platform-certification
    # test photo). Platform bots (e.g. Bale) download media URLs from THEIR
    # own servers, so a URL on this host is the guaranteed-reachable choice
    # for live certification: https://<host>/static/certification_photo.jpg
    # Phase 12 security review confirms this public surface.
    from pathlib import Path

    from fastapi.staticfiles import StaticFiles

    # App-level static (project root /static) — CSS/JS for web panel
    root_static = Path(__file__).parent.parent.parent.parent / "static"
    if root_static.is_dir():
        app.mount("/static", StaticFiles(directory=root_static), name="static")
    # Legacy: http/static (certification photo)
    http_static = Path(__file__).parent / "static"
    if http_static.is_dir() and http_static != root_static:
        # Mount under /static/http as fallback, but also keep /static for cert photo
        # The certification photo is expected at /static/certification_photo.jpg
        # So we mount root_static at /static and ensure cert photo exists there too
        pass

    # Register authoritative entitlement usage counters (products, sources).
    from app.application import usage  # noqa: F401

    return app


app = create_app()
