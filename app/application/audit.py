"""Audit trail service (foundation for the auditability requirement).

Every security-sensitive action records: actor, business, action, target,
outcome, correlation ID, and minimal metadata. Secrets and unnecessary
payloads are NEVER recorded (security threat model section 14).
"""

from __future__ import annotations

import contextvars
import uuid
from collections.abc import Mapping

from sqlalchemy.orm import Session

from app.domain import enums
from app.infrastructure.db.models import AuditLog

_correlation_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "correlation_id", default=""
)


def set_correlation_id(value: str) -> contextvars.Token[str]:
    return _correlation_id_var.set(value)


def get_correlation_id() -> str:
    """Current request correlation id, generating one if absent."""
    value = _correlation_id_var.get()
    if not value:
        value = uuid.uuid4().hex
        _correlation_id_var.set(value)
    return value


class AuditService:
    """Records audit entries on the ambient session (flushed with the commit)."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def record(
        self,
        *,
        action: str,
        outcome: enums.AuditOutcome = enums.AuditOutcome.SUCCESS,
        actor_user_id: object | None = None,
        business_id: object | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        meta: Mapping[str, object] | None = None,
    ) -> AuditLog:
        entry = AuditLog(
            actor_user_id=actor_user_id,  # type: ignore[arg-type]
            business_id=business_id,  # type: ignore[arg-type]
            action=action[:80],
            target_type=target_type[:40] if target_type else None,
            target_id=str(target_id)[:64] if target_id is not None else None,
            outcome=outcome,
            correlation_id=get_correlation_id(),
            meta_data=dict(meta) if meta else None,
        )
        self.db.add(entry)
        self.db.flush()
        return entry
