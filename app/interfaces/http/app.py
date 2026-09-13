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
        checks: dict[str, str] = {"database": "ok"}
        healthy = True
        try:
            from app.infrastructure.db import get_engine

            with get_engine().connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception as exc:  # pragma: no cover - environment dependent
            healthy = False
            checks["database"] = f"unavailable: {type(exc).__name__}"
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

    return app


app = create_app()
