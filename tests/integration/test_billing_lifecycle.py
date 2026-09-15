"""Subscription / payment / credit lifecycle (full-stack, DB-backed).

Covers the failure-first matrix for Phase 2: happy path, duplicates,
expiry/grace, plan change, downgrade over-limit, suspension, refund,
cross-tenant isolation, and audit.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.application import subscription as subsvc
from app.domain import enums
from app.domain.entitlements import register_usage_counter
from app.infrastructure.db.models import AuditLog, Membership, Subscription
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration

PASSWORD = "correct-horse-battery-1"


def _new_app_for(client: TestClient):
    return client.app


def _client_for(db_client: TestClient) -> TestClient:
    return TestClient(_new_app_for(db_client))


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


def _operator(db_client: TestClient, db_session: Session) -> TestClient:
    from tests.conftest import make_super_admin

    make_super_admin(db_session, "operator@example.com")
    client = _client_for(db_client)
    assert (
        client.post(
            "/api/v1/auth/login",
            json={"email": "operator@example.com", "password": PASSWORD},
        ).status_code
        == 200
    )
    return client


def _create_business(client: TestClient, name: str = "Acme") -> dict:
    resp = client.post("/api/v1/businesses", json={"name": name, "business_type_key": "general"})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _request(client: TestClient, bid: str, plan: str = "starter") -> dict:
    resp = client.post(f"/api/v1/businesses/{bid}/subscriptions", json={"plan_code": plan})
    assert resp.status_code == 202, resp.text
    return resp.json()


def _verify(client: TestClient, sid: str, **extra) -> dict:
    body = {"reference": "IR-TEST-1"}
    body.update(extra)
    resp = client.post(f"/api/v1/subscriptions/{sid}/payments/verify", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _audit_actions(db_session: Session) -> list[str]:
    return list(db_session.scalars(select(AuditLog.action)).all())


def _parse_dt(value: str) -> datetime:
    """Parse an ISO datetime, normalizing aware/naive to naive UTC."""
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC).replace(tzinfo=None)
    return dt


def test_full_lifecycle_request_verify_active(
    db_client: TestClient, db_session: Session
) -> None:
    _register_login(db_client, "owner@example.com")
    operator = _operator(db_client, db_session)
    bid = _create_business(db_client)["business_id"]

    # Owner requests the starter plan.
    sub = _request(db_client, bid)
    assert sub["status"] == "PENDING"
    assert sub["plan_code"] == "starter"
    sid = sub["subscription_id"]

    # Status endpoint shows the pending payment.
    status = db_client.get(f"/api/v1/businesses/{bid}/subscription").json()
    assert status["subscription"]["status"] == "PENDING"
    assert status["pending_payment"]["expected_amount"] > 0
    assert status["entitlements"]["has_active"] is False

    # Operator verifies the manual payment -> ACTIVE + period + monthly credits.
    result = _verify(operator, sid)
    active = result["subscription"]
    assert active["status"] == "ACTIVE"
    assert active["period_start"] is not None
    assert active["period_end"] is not None
    assert result["payment"]["status"] == "VERIFIED"
    assert result["payment"]["amount_paid"] == result["payment"]["expected_amount"]

    credits = db_client.get(f"/api/v1/businesses/{bid}/credits").json()
    monthly = [p for p in credits["pools"] if p["pool_type"] == "MONTHLY"]
    assert len(monthly) == 1
    assert monthly[0]["remaining"] == 50  # starter plan grant

    status = db_client.get(f"/api/v1/businesses/{bid}/subscription").json()
    assert status["entitlements"]["has_active"] is True
    assert status["entitlements"]["product_limit"] == 100

    # Audit: request + verification recorded.
    actions = _audit_actions(db_session)
    assert "subscription.requested" in actions
    assert "payment.verified" in actions
    assert "credit.granted" in actions


def test_verify_requires_super_admin(
    db_client: TestClient, db_session: Session
) -> None:
    _register_login(db_client, "owner@example.com")
    bid = _create_business(db_client)["business_id"]
    sid = _request(db_client, bid)["subscription_id"]

    # Non-super-admin (even the business owner) cannot verify.
    assert db_client.post(
        f"/api/v1/subscriptions/{sid}/payments/verify", json={"reference": "x"}
    ).status_code == 403
    anon = _client_for(db_client)
    assert anon.post(
        f"/api/v1/subscriptions/{sid}/payments/verify", json={"reference": "x"}
    ).status_code == 401


def test_double_verify_and_duplicate_request_rejected(
    db_client: TestClient, db_session: Session
) -> None:
    _register_login(db_client, "owner@example.com")
    operator = _operator(db_client, db_session)
    bid = _create_business(db_client)["business_id"]
    sid = _request(db_client, bid)["subscription_id"]

    # A second request while a payment is pending -> 409.
    dup = db_client.post(f"/api/v1/businesses/{bid}/subscriptions", json={"plan_code": "starter"})
    assert dup.status_code == 409

    # Verify once, then again: no pending payment left -> 404 (no probing).
    _verify(operator, sid)
    again = operator.post(
        f"/api/v1/subscriptions/{sid}/payments/verify", json={"reference": "y"}
    )
    assert again.status_code == 404


def test_cancel_pending_subscription(
    db_client: TestClient, db_session: Session
) -> None:
    _register_login(db_client, "owner@example.com")
    bid = _create_business(db_client)["business_id"]
    sid = _request(db_client, bid)["subscription_id"]

    cancelled = db_client.post(f"/api/v1/subscriptions/{sid}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "CANCELLED"

    # CANCELLED is terminal: no live subscription, history kept.
    status = db_client.get(f"/api/v1/businesses/{bid}/subscription").json()
    assert status["subscription"] is None
    assert status["latest"]["status"] == "CANCELLED"

    # A fresh purchase starts a NEW subscription row.
    sid2 = _request(db_client, bid)["subscription_id"]
    assert sid2 != sid
    history = db_client.get(f"/api/v1/businesses/{bid}/subscriptions").json()
    assert {s["subscription_id"] for s in history} == {sid, sid2}


def test_prepaid_renewal_extends_period(
    db_client: TestClient, db_session: Session
) -> None:
    _register_login(db_client, "owner@example.com")
    operator = _operator(db_client, db_session)
    bid = _create_business(db_client)["business_id"]
    sid = _request(db_client, bid)["subscription_id"]
    first = _verify(operator, sid)
    start1 = first["subscription"]["period_start"]
    end1 = first["subscription"]["period_end"]

    # Owner requests renewal; operator verifies while still ACTIVE.
    _request(db_client, bid)
    second = _verify(operator, sid)
    assert second["subscription"]["status"] == "ACTIVE"
    # Continuous service: same period start (normalize tz-aware/naive forms).
    assert _parse_dt(start1) == _parse_dt(second["subscription"]["period_start"])
    delta = _parse_dt(second["subscription"]["period_end"]) - _parse_dt(end1)
    # Extended by roughly one month.
    assert 28 * 24 * 3600 <= delta.total_seconds() <= 32 * 24 * 3600


def test_grace_then_renewal_crosses_month(
    db_client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Renewal crossing a month boundary: old pool expires, new pool granted."""
    _register_login(db_client, "owner@example.com")
    operator = _operator(db_client, db_session)
    bid = _create_business(db_client)["business_id"]
    sid = _request(db_client, bid)["subscription_id"]

    # Deterministic clock: activation in September, renewal in October.
    monkeypatch.setattr(subsvc, "_utcnow", lambda: datetime(2026, 9, 10, 12, 0, tzinfo=UTC))
    _verify(operator, sid)

    # Fast-forward: period already ended (back to the real clock for cycles).
    monkeypatch.undo()
    sub = db_session.get(Subscription, uuid.UUID(sid))
    sub.period_end = datetime.now(UTC) - timedelta(days=1)
    db_session.commit()

    # Scheduler moves it to GRACE (grace_end = period_end + 7 days).
    result = subsvc.process_subscription_cycles(db_session)
    db_session.commit()
    assert result == {"grace_entered": 1, "expired": 0}
    assert sub.status == "GRACE"
    assert sub.grace_end is not None

    # Entitlements remain usable during grace.
    status = db_client.get(f"/api/v1/businesses/{bid}/subscription").json()
    assert status["entitlements"]["has_active"] is True

    # Renewal during grace (October): verify -> ACTIVE with a fresh period;
    # the September pool expires, the October pool is granted.
    _request(db_client, bid)
    monkeypatch.setattr(subsvc, "_utcnow", lambda: datetime(2026, 10, 5, 12, 0, tzinfo=UTC))
    renewed = _verify(operator, sid)
    monkeypatch.undo()
    assert renewed["subscription"]["status"] == "ACTIVE"

    pools = db_client.get(f"/api/v1/businesses/{bid}/credits").json()["pools"]
    monthly = {p["period_label"]: p for p in pools if p["pool_type"] == "MONTHLY"}
    assert set(monthly) == {"2026-09", "2026-10"}
    assert monthly["2026-09"]["remaining"] == 0  # expired at rollover
    assert monthly["2026-10"]["remaining"] == 50  # freshly granted

    grace_rows = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "subscription.grace_entered")
    ).all()
    assert len(grace_rows) == 1


def test_renewal_same_month_reuses_pool_no_double_grant(
    db_client: TestClient, db_session: Session
) -> None:
    """Renewing inside the same calendar month must NOT double the grant."""
    _register_login(db_client, "owner@example.com")
    operator = _operator(db_client, db_session)
    bid = _create_business(db_client)["business_id"]
    sid = _request(db_client, bid)["subscription_id"]
    _verify(operator, sid)

    # Period ends, grace, renewal — all within the current month.
    sub = db_session.get(Subscription, uuid.UUID(sid))
    sub.period_end = datetime.now(UTC) - timedelta(days=1)
    db_session.commit()
    subsvc.process_subscription_cycles(db_session)
    db_session.commit()
    _request(db_client, bid)
    renewed = _verify(operator, sid)
    assert renewed["subscription"]["status"] == "ACTIVE"

    pools = db_client.get(f"/api/v1/businesses/{bid}/credits").json()["pools"]
    monthly = [p for p in pools if p["pool_type"] == "MONTHLY"]
    assert len(monthly) == 1
    assert monthly[0]["remaining"] == 50  # original grant, not doubled
    assert monthly[0]["granted_total"] == 50


def test_grace_expiry_blocks_entitlements(
    db_client: TestClient, db_session: Session
) -> None:
    _register_login(db_client, "owner@example.com")
    operator = _operator(db_client, db_session)
    bid = _create_business(db_client)["business_id"]
    sid = _request(db_client, bid)["subscription_id"]
    _verify(operator, sid)

    sub = db_session.get(Subscription, uuid.UUID(sid))
    sub.period_end = datetime.now(UTC) - timedelta(days=10)  # past period AND grace
    db_session.commit()

    result = subsvc.process_subscription_cycles(db_session)
    db_session.commit()
    # One pass: ACTIVE->GRACE, then GRACE->EXPIRED (grace_end already past).
    assert result["expired"] == 1
    assert sub.status == "EXPIRED"

    status = db_client.get(f"/api/v1/businesses/{bid}/subscription").json()
    assert status["subscription"] is None  # terminal: not "live"
    assert status["latest"]["status"] == "EXPIRED"
    assert status["entitlements"]["has_active"] is False

    # Unused monthly credits were expired (ledger intact).
    pools = db_client.get(f"/api/v1/businesses/{bid}/credits").json()["pools"]
    assert all(p["remaining"] == 0 for p in pools)

    # A new purchase after expiry starts a fresh cycle.
    sid2 = _request(db_client, bid)["subscription_id"]
    assert sid2 != sid
    result2 = _verify(operator, sid2)
    assert result2["subscription"]["status"] == "ACTIVE"


def test_plan_change_upgrade(
    db_client: TestClient, db_session: Session
) -> None:
    operator = _operator(db_client, db_session)
    pro = operator.post(
        "/api/v1/plans",
        json={"code": "pro", "name": "Pro", "price": 5_000_000, "product_limit": 500},
    ).json()
    _register_login(db_client, "owner@example.com")
    bid = _create_business(db_client)["business_id"]
    sid = _request(db_client, bid)["subscription_id"]
    _verify(operator, sid)

    # Upgrade request (different plan) then verification.
    changed = _request(db_client, bid, plan="pro")
    assert changed["status"] == "ACTIVE"  # live subscription, pending change
    assert changed["pending_plan_code"] == "pro"
    result = _verify(operator, sid)
    assert result["subscription"]["plan_code"] == "pro"
    assert result["subscription"]["pending_plan_code"] is None

    status = db_client.get(f"/api/v1/businesses/{bid}/subscription").json()
    assert status["entitlements"]["product_limit"] == 500
    assert "subscription.plan_changed" in _audit_actions(db_session)
    assert pro["plan_id"] is not None


def test_downgrade_blocks_new_usage_over_limit(
    db_client: TestClient, db_session: Session
) -> None:
    operator = _operator(db_client, db_session)
    operator.post(
        "/api/v1/plans",
        json={"code": "basic", "name": "Basic", "price": 490_000, "product_limit": 10},
    )
    _register_login(db_client, "owner@example.com")
    bid = _create_business(db_client)["business_id"]
    sid = _request(db_client, bid)["subscription_id"]
    _verify(operator, sid)

    # Simulate 50 existing products (Phase 3 registers real counters).
    business_uuid = uuid.UUID(bid)
    register_usage_counter("products", lambda db, business_id: 50)
    try:
        from app.application import entitlements as entsvc

        # Under starter (limit 100): fine.
        ent = entsvc.require_entitlement(
            db_session, business_id=business_uuid, limit_key="products"
        )
        assert ent.has_active

        # Downgrade to basic (limit 10) with 50 products in use.
        _request(db_client, bid, plan="basic")
        _verify(operator, sid)
        from app.domain.errors import EntitlementDenied

        with pytest.raises(EntitlementDenied):
            entsvc.require_entitlement(
                db_session, business_id=business_uuid, limit_key="products"
            )
        # Viewing entitlements still works (UI layer); only new work is blocked.
        status = db_client.get(f"/api/v1/businesses/{bid}/subscription").json()
        assert status["entitlements"]["product_limit"] == 10
    finally:
        from app.domain import entitlements as d

        d._usage_counters.pop("products", None)


def test_suspend_reactivates_within_period(
    db_client: TestClient, db_session: Session
) -> None:
    operator = _operator(db_client, db_session)
    _register_login(db_client, "owner@example.com")
    bid = _create_business(db_client)["business_id"]
    sid = _request(db_client, bid)["subscription_id"]
    _verify(operator, sid)

    suspended = operator.post(
        f"/api/v1/subscriptions/{sid}/suspend", json={"reason": "dispute"}
    )
    assert suspended.status_code == 200, suspended.text
    assert suspended.json()["status"] == "SUSPENDED"

    status = db_client.get(f"/api/v1/businesses/{bid}/subscription").json()
    assert status["entitlements"]["has_active"] is False

    reactivated = operator.post(f"/api/v1/subscriptions/{sid}/reactivate")
    assert reactivated.status_code == 200, reactivated.text
    assert reactivated.json()["status"] == "ACTIVE"

    status = db_client.get(f"/api/v1/businesses/{bid}/subscription").json()
    assert status["entitlements"]["has_active"] is True


def test_refund_terminal_keeps_purchased_credits(
    db_client: TestClient, db_session: Session
) -> None:
    operator = _operator(db_client, db_session)
    _register_login(db_client, "owner@example.com")
    bid = _create_business(db_client)["business_id"]
    sid = _request(db_client, bid)["subscription_id"]
    _verify(operator, sid)

    # Operator top-ups purchased credits; some consumed.
    topup = operator.post(
        f"/api/v1/subscriptions/{sid}/credits/topup", json={"amount": 30, "note": "promo"}
    )
    assert topup.status_code == 200, topup.text
    from app.application import credits as credits_use

    # Consume 60: drains the 50 monthly pool first (priority), then 10 from
    # the purchased pool.
    credits_use.consume_credit(
        db_session, business_id=uuid.UUID(bid), amount=60, idempotency_key="refund-test"
    )
    db_session.commit()

    refunded = operator.post(
        f"/api/v1/subscriptions/{sid}/refund", json={"reason": "customer request"}
    )
    assert refunded.status_code == 200, refunded.text
    assert refunded.json()["status"] == "REFUNDED"

    pools = {
        p["pool_type"]: p
        for p in db_client.get(f"/api/v1/businesses/{bid}/credits").json()["pools"]
    }
    assert pools["MONTHLY"]["remaining"] == 0  # expired on refund
    assert pools["PURCHASED"]["remaining"] == 20  # 30 - 10, kept

    status = db_client.get(f"/api/v1/businesses/{bid}/subscription").json()
    assert status["entitlements"]["has_active"] is False
    assert "subscription.refunded" in _audit_actions(db_session)


def test_cross_tenant_isolation(db_client: TestClient, db_session: Session) -> None:
    _register_login(db_client, "owner-a@example.com")
    bid_a = _create_business(db_client, "A Co")["business_id"]
    sid_a = _request(db_client, bid_a)["subscription_id"]

    # Owner B (separate client) cannot see or act on A's subscription.
    client_b = _client_for(db_client)
    _register_login(client_b, "owner-b@example.com")
    assert client_b.get(f"/api/v1/businesses/{bid_a}/subscription").status_code == 404
    assert (
        client_b.post(
            f"/api/v1/businesses/{bid_a}/subscriptions", json={"plan_code": "starter"}
        ).status_code
        == 404
    )
    assert client_b.post(f"/api/v1/subscriptions/{sid_a}/cancel").status_code == 404


def test_amount_mismatch_requires_note(
    db_client: TestClient, db_session: Session
) -> None:
    operator = _operator(db_client, db_session)
    _register_login(db_client, "owner@example.com")
    bid = _create_business(db_client)["business_id"]
    sid = _request(db_client, bid)["subscription_id"]
    expected = db_client.get(f"/api/v1/businesses/{bid}/subscription").json()[
        "pending_payment"
    ]["expected_amount"]

    # Mismatch without note -> 422.
    bad = operator.post(
        f"/api/v1/subscriptions/{sid}/payments/verify",
        json={"amount_paid": expected - 1, "reference": "r"},
    )
    assert bad.status_code == 422

    # Mismatch with note -> accepted, audited as discrepancy.
    ok_resp = operator.post(
        f"/api/v1/subscriptions/{sid}/payments/verify",
        json={"amount_paid": expected - 1, "reference": "r", "note": "discounted by owner"},
    )
    assert ok_resp.status_code == 200, ok_resp.text
    ok = ok_resp.json()
    assert ok["payment"]["amount_paid"] == expected - 1
    rows = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "payment.verified")
    ).all()
    assert any(r.meta_data and r.meta_data.get("amount_mismatch") for r in rows)


def test_admin_without_subscription_permission_cannot_request(
    db_client: TestClient, db_session: Session
) -> None:
    """RBAC layer: only OWNER (subscription.manage) may request billing."""
    _register_login(db_client, "owner@example.com")
    bid = _create_business(db_client)["business_id"]

    # Directly create an ADMIN member (service-layer bypass for test setup).
    from app.application import auth

    admin_user = auth.register_user(
        db_session, email="admin2@example.com", password=PASSWORD, display_name="A2"
    )
    db_session.add(
        Membership(
            user_id=admin_user.user_id,
            business_id=uuid.UUID(bid),
            role=enums.MembershipRole.ADMIN.value,
            status=enums.MembershipStatus.ACTIVE.value,
            permissions=None,
            approved_by=admin_user.user_id,
            approved_at=datetime.now(UTC),
        )
    )
    db_session.commit()

    admin_client = _client_for(db_client)
    assert (
        admin_client.post(
            "/api/v1/auth/login",
            json={"email": "admin2@example.com", "password": PASSWORD},
        ).status_code
        == 200
    )
    resp = admin_client.post(
        f"/api/v1/businesses/{bid}/subscriptions", json={"plan_code": "starter"}
    )
    assert resp.status_code == 403  # subscription.manage is owner-only
