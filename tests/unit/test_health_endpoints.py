"""Liveness/readiness contract (OBSERVABILITY spec).

Liveness must be 200 when the process is up. Readiness must expose per-
dependency checks and be 503 when a required dependency is unreachable.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_healthz_liveness_is_ok(client: TestClient) -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "version" in body
    assert body["environment"]


def test_readyz_contract(client: TestClient) -> None:
    resp = client.get("/readyz")
    # Ready (200) if a local DB is reachable, otherwise 503. The contract is:
    # the response always carries a boolean ``ready`` and a ``checks`` map.
    assert resp.status_code in (200, 503)
    body = resp.json()
    assert isinstance(body["ready"], bool)
    assert isinstance(body["checks"], dict)
    assert "database" in body["checks"]
    # Invariant: the status code must be consistent with the ``ready`` flag.
    assert (resp.status_code == 200) is body["ready"]


def test_readyz_reports_database_check(client: TestClient) -> None:
    body = client.get("/readyz").json()
    db_check = body["checks"]["database"]
    assert db_check == "ok" or db_check.startswith("unavailable")


def test_readyz_is_ok_when_database_is_live(db_client: TestClient) -> None:
    """Regression: with a reachable engine, /readyz must be 200/ok — never a
    handler-level failure masquerading as a DB check (e.g. an ImportError in
    the readiness handler must surface as a real 500, not 'unavailable')."""
    resp = db_client.get("/readyz")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ready"] is True
    assert body["checks"]["database"] == "ok"
