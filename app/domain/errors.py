"""Domain error types.

Use cases raise these; HTTP handlers map them to status codes. Error types
carry a stable machine-readable ``code`` for logs and clients (per the error
taxonomy: machine code + human-readable diagnosis).
"""

from __future__ import annotations


class DomainError(Exception):
    """Base class for expected domain failures (not bugs)."""

    code: str = "domain_error"
    http_status: int = 400
    message: str = "Domain error"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.message)
        if message:
            self.message = message


class ValidationError(DomainError):
    code = "validation_error"
    http_status = 422
    message = "Invalid input"


class ConflictError(DomainError):
    code = "conflict"
    http_status = 409
    message = "Conflict with current state"


class NotFoundError(DomainError):
    code = "not_found"
    http_status = 404
    message = "Resource not found"


class AuthenticationError(DomainError):
    code = "authentication_failed"
    http_status = 401
    message = "Authentication failed"


class AccountLocked(AuthenticationError):
    code = "account_locked"
    http_status = 429
    message = "Account temporarily locked due to repeated failed attempts"


class InvalidCredentials(AuthenticationError):
    code = "invalid_credentials"
    http_status = 401
    message = "Invalid email or password"


class AuthorizationError(DomainError):
    code = "forbidden"
    http_status = 403
    message = "Insufficient permissions"


class BusinessNotAccessible(AuthorizationError):
    """Cross-tenant access attempt: fail closed, do not reveal existence.

    Reported as 404 (not 403) so the client cannot probe which businesses
    exist (security threat model: do not expose whether a resource belongs
    to another customer).
    """

    code = "business_not_found"
    http_status = 404
    message = "Business not found"
