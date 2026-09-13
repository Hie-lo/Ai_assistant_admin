"""FastAPI application factory and health endpoints.

Liveness (``/healthz``) and readiness (``/readyz``) are intentionally
separate, per OBSERVABILITY_BACKUP_DR_SPECIFICATION_V1: a live process that
cannot reach its database/queue must NOT be reported as fully ready.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app import __version__
from app.config.settings import get_settings


def create_app() -> FastAPI:
    """Build the FastAPI application (dependency-injected, testable)."""
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        debug=settings.debug,
        docs_url="/api/docs" if not settings.is_prod else None,
        redoc_url=None,
    )

    @app.get("/healthz", tags=["health"])
    def healthz() -> dict[str, str]:
        """Liveness: the process is up. Does NOT check dependencies."""
        return {"status": "ok", "version": __version__, "environment": settings.environment}

    @app.get("/readyz", tags=["health"])
    def readyz() -> JSONResponse:
        """Readiness: liveness PLUS dependency checks (database).

        Returns 503 when a required dependency is unreachable so that a load
        balancer / orchestrator will not route traffic to an unready instance.
        """
        checks: dict[str, str] = {"database": "ok"}
        healthy = True
        try:
            from app.infrastructure.db import get_engine

            with get_engine().connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception as exc:  # pragma: no cover - depends on environment
            healthy = False
            checks["database"] = f"unavailable: {type(exc).__name__}"

        status_code = 200 if healthy else 503
        return JSONResponse(status_code=status_code, content={"ready": healthy, "checks": checks})

    return app


app = create_app()
