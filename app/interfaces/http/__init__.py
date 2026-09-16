"""HTTP interface (FastAPI).

Server-rendered Jinja2 + HTMX pages will be added in Phase 9. This baseline
exposes the application factory and liveness/readiness health endpoints.
"""

from app.interfaces.http.app import create_app

__all__ = ["create_app"]
