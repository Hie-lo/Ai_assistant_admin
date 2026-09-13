"""Audit trail tests: sensitive actions are recorded with context."""

from __future__ import annotations

from app.infrastructure.db.models import AuditLog
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

PASSWORD = "correct-horse-battery-1"


def _actions(db: Session) -> list[str]:
    rows = db.scalars(select(AuditLog).order_by(AuditLog.created_at)).all()
    return [r.action for r in rows]


def test_audit_records_auth_and_business_events(
    db_client: TestClient, db_session: Session
) -> None:
    db_client.post(
        "/api/v1/auth/register",
        json={"email": "audit@example.com", "password": PASSWORD, "display_name": "A"},
    )
    db_client.post("/api/v1/auth/login", json={"email": "audit@example.com", "password": PASSWORD})
    biz = db_client.post(
        "/api/v1/businesses", json={"name": "AuditCo", "business_type_key": "general"}
    )
    assert biz.status_code == 201
    db_client.post("/api/v1/auth/logout")

    actions = _actions(db_session)
    for expected in ("auth.register", "auth.login", "business.created", "auth.session_revoked"):
        assert expected in actions, f"missing audit action {expected} in {actions}"

    # The business event carries the business id and a correlation id.
    row = db_session.scalar(select(AuditLog).where(AuditLog.action == "business.created"))
    assert row is not None
    assert row.business_id is not None
    assert row.correlation_id
    assert row.outcome == "SUCCESS"
    assert row.actor_user_id is not None


def test_failed_login_is_audited(db_client: TestClient, db_session: Session) -> None:
    db_client.post(
        "/api/v1/auth/register",
        json={"email": "fail@example.com", "password": PASSWORD, "display_name": "F"},
    )
    db_client.post(
        "/api/v1/auth/login", json={"email": "fail@example.com", "password": "wrong-password-1"}
    )
    actions = _actions(db_session)
    assert "auth.login_failed" in actions
    row = db_session.scalar(select(AuditLog).where(AuditLog.action == "auth.login_failed"))
    assert row.outcome == "FAILURE"


def test_admin_request_events_are_audited(db_client: TestClient, db_session: Session) -> None:
    db_client.post(
        "/api/v1/auth/register",
        json={"email": "o@example.com", "password": PASSWORD, "display_name": "O"},
    )
    db_client.post("/api/v1/auth/login", json={"email": "o@example.com", "password": PASSWORD})
    bid = db_client.post(
        "/api/v1/businesses", json={"name": "Co", "business_type_key": "general"}
    ).json()["business_id"]
    code = db_client.post(f"/api/v1/businesses/{bid}/invites", json={}).json()["code"]

    cand = TestClient(db_client.app)
    cand.post(
        "/api/v1/auth/register",
        json={"email": "c@example.com", "password": PASSWORD, "display_name": "C"},
    )
    cand.post("/api/v1/auth/login", json={"email": "c@example.com", "password": PASSWORD})
    rid = cand.post(
        "/api/v1/admin-requests", json={"method": "invite_code", "code": code}
    ).json()["request_id"]
    db_client.post(f"/api/v1/businesses/{bid}/admin-requests/{rid}/approve")

    actions = _actions(db_session)
    assert "admin.invite_created" in actions
    assert "admin.request_submitted" in actions
    assert "admin.request_approved" in actions
