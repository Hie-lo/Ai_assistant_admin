"""Plan catalog management: operator-only, validated, audited."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration

PASSWORD = "correct-horse-battery-1"


def _new_app_for(client: TestClient):
    return client.app


def _register_login(client: TestClient, email: str, name: str = "T") -> None:
    assert (
        client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": PASSWORD, "display_name": name},
        ).status_code
        == 201
    )
    assert (
        client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD}).status_code
        == 200
    )


def _make_operator(client: TestClient, db_session: Session) -> TestClient:
    from tests.conftest import make_super_admin

    make_super_admin(db_session, "operator@example.com")
    op = TestClient(_new_app_for(client))
    resp = op.post(
        "/api/v1/auth/login",
        json={"email": "operator@example.com", "password": PASSWORD},
    )
    assert resp.status_code == 200, resp.text
    return op


def test_starter_plan_seeded(db_client: TestClient, db_session: Session) -> None:
    operator = _make_operator(db_client, db_session)
    resp = operator.get("/api/v1/plans")
    assert resp.status_code == 200, resp.text
    codes = [p["code"] for p in resp.json()]
    assert "starter" in codes
    starter = next(p for p in resp.json() if p["code"] == "starter")
    assert starter["currency"] == "IRT"
    assert starter["price"] > 0
    assert starter["product_limit"] is not None


def test_operator_can_create_and_update_plan(db_client: TestClient, db_session: Session) -> None:
    operator = _make_operator(db_client, db_session)

    created = operator.post(
        "/api/v1/plans",
        json={"code": "pro", "name": "Pro", "price": 2_990_000, "product_limit": 1000},
    )
    assert created.status_code == 201, created.text
    plan_id = created.json()["plan_id"]
    assert created.json()["product_limit"] == 1000

    # Duplicate code rejected.
    dup = operator.post(
        "/api/v1/plans", json={"code": "pro", "name": "Pro2", "price": 1}
    )
    assert dup.status_code == 422

    # Update price + limits.
    updated = operator.patch(
        f"/api/v1/plans/{plan_id}", json={"price": 3_490_000, "ai_monthly_credits": 200}
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["price"] == 3_490_000
    assert updated.json()["ai_monthly_credits"] == 200

    # Inactive plans hidden from the default listing.
    hidden = operator.patch(f"/api/v1/plans/{plan_id}", json={"is_active": False})
    assert hidden.status_code == 200
    default_codes = [p["code"] for p in operator.get("/api/v1/plans").json()]
    all_codes = [p["code"] for p in operator.get("/api/v1/plans?include_inactive=true").json()]
    assert "pro" not in default_codes
    assert "pro" in all_codes


def test_plan_validation_errors(db_client: TestClient, db_session: Session) -> None:
    operator = _make_operator(db_client, db_session)
    bad_price = operator.post(
        "/api/v1/plans", json={"code": "bad", "name": "Bad", "price": -5}
    )
    assert bad_price.status_code == 422
    bad_code = operator.post(
        "/api/v1/plans", json={"code": "UPPER CASE!", "name": "Bad", "price": 1}
    )
    assert bad_code.status_code == 422
    missing = operator.post("/api/v1/plans", json={"name": "No code"})
    assert missing.status_code == 422


def test_plan_management_requires_super_admin(db_client: TestClient, db_session: Session) -> None:
    _register_login(db_client, "owner@example.com")
    # Plain business owner (no super admin flag) -> 403.
    assert db_client.get("/api/v1/plans").status_code == 403
    assert db_client.post("/api/v1/plans", json={"code": "x", "name": "X"}).status_code == 403
    # Unauthenticated -> 401.
    anon = TestClient(_new_app_for(db_client))
    assert anon.get("/api/v1/plans").status_code == 401
