"""Authentication flow tests (DB-backed: SQLite locally, PostgreSQL in CI)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

PASSWORD = "correct-horse-battery-1"

pytestmark = pytest.mark.integration


def _register(client: TestClient, email: str, name: str = "Test") -> None:
    resp = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "display_name": name},
    )
    assert resp.status_code == 201, resp.text


def _login(client: TestClient, email: str, password: str = PASSWORD) -> int:
    return client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    ).status_code


def test_register_login_me_roundtrip(db_client: TestClient) -> None:
    _register(db_client, "owner@example.com", "Owner")
    assert _login(db_client, "owner@example.com") == 200
    me = db_client.get("/api/v1/me")
    assert me.status_code == 200
    assert me.json()["user"]["email"] == "owner@example.com"


def test_register_duplicate_email_conflict(db_client: TestClient) -> None:
    _register(db_client, "dup@example.com")
    resp = db_client.post(
        "/api/v1/auth/register",
        json={"email": "DUP@example.com ", "password": PASSWORD, "display_name": "X"},
    )
    assert resp.status_code == 409  # normalized: uppercase+whitespace same account


def test_register_weak_password_rejected(db_client: TestClient) -> None:
    resp = db_client.post(
        "/api/v1/auth/register",
        json={"email": "weak@example.com", "password": "short", "display_name": "X"},
    )
    assert resp.status_code == 422


def test_wrong_password_locks_after_bounded_attempts(db_client: TestClient) -> None:
    _register(db_client, "lock@example.com")
    for _ in range(5):
        assert _login(db_client, "lock@example.com", "totally-wrong-pass") == 401
    # 6th attempt: locked (even with the correct password) -> 429
    assert _login(db_client, "lock@example.com") == 429


def test_logout_revokes_session(db_client: TestClient) -> None:
    _register(db_client, "out@example.com")
    assert _login(db_client, "out@example.com") == 200
    assert db_client.get("/api/v1/me").status_code == 200
    assert db_client.post("/api/v1/auth/logout").status_code == 204
    assert db_client.get("/api/v1/me").status_code == 401


def test_unauthenticated_me_rejected(client: TestClient) -> None:
    # No session cookie -> 401 (no DB access happens before the rejection).
    assert client.get("/api/v1/me").status_code == 401


def test_expired_or_garbage_token_rejected(db_client: TestClient) -> None:
    resp = db_client.get("/api/v1/me", cookies={"ai_session": "garbage-token-value"})
    assert resp.status_code == 401


def test_session_listing_and_manual_revoke(db_client: TestClient) -> None:
    _register(db_client, "sess@example.com")
    assert _login(db_client, "sess@example.com") == 200
    sessions = db_client.get("/api/v1/account/sessions")
    assert sessions.status_code == 200
    rows = sessions.json()
    assert len(rows) == 1
    assert rows[0]["is_active"] is True
    sid = rows[0]["session_id"]
    assert db_client.post(f"/api/v1/account/sessions/{sid}/revoke").status_code == 204
    # The active session was revoked -> subsequent auth fails.
    assert db_client.get("/api/v1/me").status_code == 401


def test_invalid_email_format_rejected(db_client: TestClient) -> None:
    resp = db_client.post(
        "/api/v1/auth/register",
        json={"email": "not-an-email", "password": PASSWORD, "display_name": "X"},
    )
    assert resp.status_code == 422


@pytest.mark.parametrize("email", ["a@b.co", "first.last+tag@sub.domain.example"])
def test_email_normalization_and_acceptance(db_client: TestClient, email: str) -> None:
    resp = db_client.post(
        "/api/v1/auth/register",
        json={"email": f"  {email.upper()}  ", "password": PASSWORD, "display_name": "N"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["email"] == email
