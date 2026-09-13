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


class EntitlementDenied(AuthorizationError):
    """The business's active subscription does not permit this operation.

    Entitlement is a layer above RBAC: the effective permission is
    role permission ∩ business policy  subscription entitlement ∩ scope.
    UI visibility is never the security boundary — this check is
    server-side and happens immediately before execution.
    """

    code = "entitlement_denied"
    http_status = 403

    def __init__(self, reason: str = "not covered by the active subscription") -> None:
        super().__init__(reason)


class CreditInsufficient(DomainError):
    """Requested AI credits exceed the available credit pools."""

    code = "credit_insufficient"
    http_status = 409

    def __init__(self, requested: int, available: int) -> None:
        super().__init__(f"Requested {requested} credits but only {available} available")
        self.requested = requested
        self.available = available


class SubscriptionTransitionInvalid(DomainError):
    """The requested subscription state transition is not allowed."""

    code = "subscription_transition_invalid"
    http_status = 409


class PaymentAlreadySettled(DomainError):
    """The payment was already verified or rejected."""

    code = "payment_already_settled"
    http_status = 409


class PendingPaymentExists(DomainError):
    """A payment is already pending for this subscription."""

    code = "pending_payment_exists"
    http_status = 409


class PlanNotFound(NotFoundError):
    code = "plan_not_found"
    message = "Plan not found"


class SubscriptionNotFound(NotFoundError):
    code = "subscription_not_found"
    message = "Subscription not found"
