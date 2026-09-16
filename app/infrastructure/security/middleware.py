"""Security middleware and utilities (Phase 10-12).

Per SECURITY_THREAT_MODEL_V1:
- Input validation (file size/type limits, parsing timeouts)
- SSRF protection for external fetches
- Rate limiting (already in monitoring middleware, extended here)
- Tenant isolation enforcement (service layer + DB constraints)
- Secret handling (never log secrets)

This module provides:
- SSRF protection for media URL fetching
- File upload validation
- Security headers middleware
"""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlparse

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

# --- SSRF Protection ---------------------------------------------------------


PRIVATE_IP_RANGES = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]

BLOCKED_SCHEMES = {"file", "ftp", "gopher", "data"}


def is_private_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
        return any(ip in net for net in PRIVATE_IP_RANGES)
    except ValueError:
        return False


def validate_external_url(url: str, allow_private: bool = False) -> tuple[bool, str]:
    """Validate an external URL for SSRF protection.

    Returns (is_valid, reason).
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return False, "invalid URL format"

    if parsed.scheme.lower() in BLOCKED_SCHEMES:
        return False, f"blocked scheme: {parsed.scheme}"

    if parsed.scheme.lower() not in ("http", "https"):
        return False, f"unsupported scheme: {parsed.scheme}"

    if not parsed.hostname:
        return False, "missing hostname"

    # Block private IPs in hostname if it's an IP
    try:
        ip = ipaddress.ip_address(parsed.hostname)
        if not allow_private and is_private_ip(str(ip)):
            return False, "private IP not allowed"
    except ValueError:
        if not allow_private and parsed.hostname.lower() in (
            "localhost",
            "127.0.0.1",
            "::1",
        ):
            return False, "localhost not allowed"

    # Block suspicious patterns
    if ".." in parsed.path or parsed.hostname.startswith("."):
        return False, "suspicious URL pattern"

    return True, "ok"


def sanitize_url_for_log(url: str) -> str:
    """Remove secrets from URL for logging."""
    try:
        parsed = urlparse(url)
        # Remove query params that might contain secrets
        return f"{parsed.scheme}://{parsed.hostname}{parsed.path[:100]}"
    except Exception:
        return url[:100]


# --- File Upload Validation --------------------------------------------------


MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10MB
ALLOWED_EXCEL_TYPES = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
    "application/octet-stream",  # Some browsers
}

ALLOWED_IMAGE_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
}


def validate_upload_file(
    filename: str, content_type: str, size: int
) -> tuple[bool, str]:
    if size > MAX_UPLOAD_SIZE:
        return False, f"file too large: {size} > {MAX_UPLOAD_SIZE}"
    if not filename:
        return False, "missing filename"
    ext = filename.lower().split(".")[-1] if "." in filename else ""
    allowed = {"xlsx", "xls", "jpg", "jpeg", "png", "webp", "gif"}
    if ext not in allowed and ext not in ("xlsx", "xls"):
        return False, f"unsupported file extension: {ext}"
    return True, "ok"


# --- Security Headers Middleware ---------------------------------------------


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        # HSTS only in prod (avoids breaking http dev)
        try:
            from app.config.settings import get_settings

            if get_settings().is_prod:
                response.headers["Strict-Transport-Security"] = (
                    "max-age=31536000; includeSubDomains"
                )
        except Exception:
            pass
        # CSP: self + htmx CDN, no unsafe-inline for scripts, unsafe-inline
        # for styles kept minimal (HTMX needs some inline styles for progress)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' https://unpkg.com; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https:; "
            "connect-src 'self'; "
            "frame-ancestors 'none';"
        )
        return response


# --- Input Sanitization ------------------------------------------------------


def sanitize_text_input(text: str, max_length: int = 1000) -> str:
    """Sanitize free-text input."""
    if not text:
        return ""
    # Remove control characters except newline, tab
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    # Truncate
    if len(text) > max_length:
        text = text[:max_length]
    return text.strip()


def validate_business_name(name: str) -> tuple[bool, str]:
    if not name or len(name.strip()) < 1:
        return False, "name is required"
    if len(name) > 160:
        return False, "name too long"
    # Allow Persian, English, numbers, spaces, some punctuation
    # Block script tags
    if "<script" in name.lower() or "javascript:" in name.lower():
        return False, "invalid characters"
    return True, "ok"
