"""Cross-interface linking tests (one-time codes, internal verify contract)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

PASSWORD = "correct-horse-battery-1"
INTERNAL = {"X-Internal-Token": "test-internal-token"}

pytestmark = pytest.mark.integration


def _register_login(client: TestClient, email: str) -> None:
    assert (
        client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": PASSWORD, "display_name": email.split("@")[0]},
        ).status_code
        == 201
    )
    assert (
        client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD}).status_code
        == 200
    )


def test_link_telegram_identity_success(db_client: TestClient) -> None:
    _register_login(db_client, "user1@example.com")
    created = db_client.post("/api/v1/account/links", json={"platform": "telegram"})
    assert created.status_code == 201, created.text
    code = created.json()["code"]
    assert len(code) == 6

    verified = db_client.post(
        "/api/v1/links/verify",
        json={"platform": "telegram", "code": code, "platform_user_id": "tg-user-1"},
        headers=INTERNAL,
    )
    assert verified.status_code == 200, verified.text

    identities = db_client.get("/api/v1/account/identities").json()
    assert len(identities) == 1
    assert identities[0]["platform"] == "TELEGRAM"
    assert identities[0]["platform_user_id"] == "tg-user-1"


def test_verify_requires_internal_token(db_client: TestClient) -> None:
    _register_login(db_client, "user2@example.com")
    code = db_client.post("/api/v1/account/links", json={"platform": "bale"}).json()["code"]
    payload = {"platform": "bale", "code": code, "platform_user_id": "bale-1"}

    # No token -> 401.
    assert db_client.post("/api/v1/links/verify", json=payload).status_code == 401
    # Wrong token -> 401.
    resp = db_client.post(
        "/api/v1/links/verify",
        json=payload,
        headers={"X-Internal-Token": "wrong-token"},
    )
    assert resp.status_code == 401


def test_verify_is_single_use(db_client: TestClient) -> None:
    _register_login(db_client, "user3@example.com")
    code = db_client.post("/api/v1/account/links", json={"platform": "telegram"}).json()["code"]
    payload = {"platform": "telegram", "code": code, "platform_user_id": "tg-3"}
    assert db_client.post("/api/v1/links/verify", json=payload, headers=INTERNAL).status_code == 200
    # Second use of the same code -> invalid (consumed).
    assert db_client.post("/api/v1/links/verify", json=payload, headers=INTERNAL).status_code == 404


def test_identity_conflict_across_accounts(db_client: TestClient) -> None:
    _register_login(db_client, "alice@example.com")
    code_a = db_client.post("/api/v1/account/links", json={"platform": "telegram"}).json()["code"]
    assert (
        db_client.post(
            "/api/v1/links/verify",
            json={"platform": "telegram", "code": code_a, "platform_user_id": "tg-shared"},
            headers=INTERNAL,
        ).status_code
        == 200
    )

    # Alice logs out; Bob tries to claim the same telegram identity.
    db_client.post("/api/v1/auth/logout")
    _register_login(db_client, "bob@example.com")
    code_b = db_client.post("/api/v1/account/links", json={"platform": "telegram"}).json()["code"]
    resp = db_client.post(
        "/api/v1/links/verify",
        json={"platform": "telegram", "code": code_b, "platform_user_id": "tg-shared"},
        headers=INTERNAL,
    )
    # Never merge accounts: explicit conflict.
    assert resp.status_code == 409
    # Bob has no identities.
    assert db_client.get("/api/v1/account/identities").json() == []


def test_relink_same_account_is_idempotent(db_client: TestClient) -> None:
    _register_login(db_client, "carol@example.com")
    code_a = db_client.post("/api/v1/account/links", json={"platform": "telegram"}).json()["code"]
    assert (
        db_client.post(
            "/api/v1/links/verify",
            json={"platform": "telegram", "code": code_a, "platform_user_id": "tg-carol"},
            headers=INTERNAL,
        ).status_code
        == 200
    )
    # Carol loses the app and re-links the same identity with a fresh code.
    code_b = db_client.post("/api/v1/account/links", json={"platform": "telegram"}).json()["code"]
    assert (
        db_client.post(
            "/api/v1/links/verify",
            json={"platform": "telegram", "code": code_b, "platform_user_id": "tg-carol"},
            headers=INTERNAL,
        ).status_code
        == 200
    )
    identities = db_client.get("/api/v1/account/identities").json()
    assert len(identities) == 1


def test_unsupported_platform_rejected(db_client: TestClient) -> None:
    _register_login(db_client, "dave@example.com")
    resp = db_client.post("/api/v1/account/links", json={"platform": "rubika"})
    assert resp.status_code == 422  # V1 linking targets: telegram/bale only
