"""Observability middleware (Phase 10).

- Records request latency histograms
- Counts rate-limit events
- Adds structured logging context (already in app.py but extended here)
- Tracks DB latency via optional hook
"""

from __future__ import annotations

import logging
import time

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.infrastructure.monitoring.metrics import inc_counter, observe_histogram

logger = logging.getLogger("app.monitoring")


class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        path = request.url.path
        method = request.method

        if path.startswith("/metrics") or path.startswith("/healthz"):
            return await call_next(request)
        if path.startswith("/readyz"):
            return await call_next(request)

        try:
            response = await call_next(request)
            duration = (time.perf_counter() - start) * 1000.0
            observe_histogram(
                "http_request_duration_ms",
                duration,
                method=method,
                path=path,
                status=str(response.status_code),
            )
            inc_counter(
                "http_requests_total",
                1,
                method=method,
                path=path,
                status=str(response.status_code),
            )

            if response.status_code == 429:
                inc_counter("http_rate_limited_total", 1, path=path)

            return response
        except Exception:
            duration = (time.perf_counter() - start) * 1000.0
            observe_histogram(
                "http_request_duration_ms",
                duration,
                method=method,
                path=path,
                status="500",
            )
            inc_counter(
                "http_requests_total",
                1,
                method=method,
                path=path,
                status="500",
            )
            logger.exception("request failed: %s %s", method, path)
            raise


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple in-memory rate limiter (Phase 10, security)."""

    def __init__(self, app, requests_per_minute: int = 60):
        super().__init__(app)
        self.requests_per_minute = requests_per_minute
        self._requests: dict[str, list[float]] = {}

    async def dispatch(self, request: Request, call_next):
        sensitive_prefixes = (
            "/api/v1/auth/login",
            "/api/v1/auth/register",
            "/api/v1/links",
            "/api/v1/businesses",
        )
        path = request.url.path
        if not any(path.startswith(p) for p in sensitive_prefixes):
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        key = f"{client_ip}:{path}"
        now = time.time()

        if key not in self._requests:
            self._requests[key] = []
        self._requests[key] = [
            t for t in self._requests[key] if now - t < 60
        ]

        if len(self._requests[key]) >= self.requests_per_minute:
            from fastapi.responses import JSONResponse

            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": "RATE_LIMITED",
                        "message": "Too many requests, please try later",
                    }
                },
            )

        self._requests[key].append(now)
        return await call_next(request)
