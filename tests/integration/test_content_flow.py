"""Phase 4 integration: presets, per-product presets, AI generation/
approval, credits, entitlements, preview, tenant isolation, permissions.

Runs the full HTTP stack against the seeded reference data (default preset
+ AI definitions), using the deterministic template provider by default.
"""

from __future__ import annotations

import uuid

import pytest
from app.infrastructure.ai import (
    AIGenerationRequest,
    AIResult,
    PermanentAIFailure,
    TransientAIFailure,
    set_ai_provider_override,
)
from app.infrastructure.db.models import (
    CreditPool,
    Membership,
    Product,
)
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration

PASSWORD = "correct-horse-battery-1"
API = "/api/v1"


# --- providers used to simulate failures ---


class AlwaysTransient:
    name = "test_transient"

    def generate(self, request: AIGenerationRequest) -> AIResult:
        raise TransientAIFailure("simulated transient outage")


class InvalidLength:
    name = "test_invalid"

    def generate(self, request: AIGenerationRequest) -> AIResult:
        return AIResult(
            text="x" * (request.max_length + 500),
            model="m",
            provider=self.name,
            prompt_chars=10,
            completion_chars=request.max_length + 500,
        )


class Permanent:
    name = "test_permanent"

    def generate(self, request: AIGenerationRequest) -> AIResult:
        raise PermanentAIFailure("simulated auth failure (401)")


# --- small builders ---


def _block(bid: str, btype: str, payload: dict, ownership: str = "SYSTEM_MANAGED") -> dict:
    return {"id": bid, "type": btype, "ownership": ownership, "payload": payload}


def _name_block() -> dict:
    return _block("n", "PRODUCT_FIELD", {"field": "name"})


def _client_for(client: TestClient) -> TestClient:
    return TestClient(client.app)


def _register_login(client: TestClient, email: str, name: str) -> None:
    assert (
        client.post(
            f"{API}/auth/register",
            json={"email": email, "password": PASSWORD, "display_name": name},
        ).status_code
        == 201
    )
    assert (
        client.post(
            f"{API}/auth/login", json={"email": email, "password": PASSWORD}
        ).status_code
        == 200
    )


def _bootstrap(db_client: TestClient, db_session: Session):
    """owner + super-admin operator + business on an ACTIVE starter plan."""
    from tests.conftest import make_super_admin

    _register_login(db_client, "owner@example.com", "Owner")
    make_super_admin(db_session, "operator@example.com")
    op = _client_for(db_client)
    assert (
        op.post(
            f"{API}/auth/login",
            json={"email": "operator@example.com", "password": PASSWORD},
        ).status_code
        == 200
    )
    bid = db_client.post(
        f"{API}/businesses", json={"name": "Acme", "business_type_key": "general"}
    ).json()["business_id"]
    sub = db_client.post(
        f"{API}/businesses/{bid}/subscriptions", json={"plan_code": "starter"}
    ).json()
    resp = op.post(
        f"{API}/subscriptions/{sub['subscription_id']}/payments/verify",
        json={"reference": "IR-TEST-1"},
    )
    assert resp.status_code == 200, resp.text
    return db_client, op, bid


def _seed_product(db_session: Session, *, bid: str, name: str = "Laptop X") -> str:
    p = Product(
        business_id=uuid.UUID(bid),
        name=name,
        category="electronics",
        description="A fast laptop.",
        price=1000,
        currency="IRT",
        stock=3,
        attributes={"color": "black"},
    )
    db_session.add(p)
    db_session.commit()
    return str(p.product_id)


def _credits_remaining(db_session: Session, *, bid: str) -> int:
    pools = db_session.scalars(
        select(CreditPool).where(CreditPool.business_id == uuid.UUID(bid))
    ).all()
    return sum(p.remaining for p in pools)


def _set_plan_product_preset_eligible(op: TestClient, *, code: str, eligible: bool) -> None:
    plans = op.get(f"{API}/plans").json()
    plan = next(p for p in plans if p["code"] == code)
    resp = op.patch(
        f"{API}/plans/{plan['plan_id']}",
        json={"product_preset_eligible": eligible},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["product_preset_eligible"] is eligible


def _generate(client: TestClient, bid: str, pid: str, key: str = "ai_description") -> dict:
    return client.post(
        f"{API}/businesses/{bid}/products/{pid}/ai/generate",
        json={"definition_key": key},
    )


# --- admin: business-type presets ---


def test_admin_preset_lifecycle_and_versioning(db_client, db_session):
    _owner, op, _bid = _bootstrap(db_client, db_session)

    # Seeded default preset is visible to the operator.
    presets = op.get(f"{API}/admin/presets").json()
    assert any(
        p["business_type_key"] == "general" and p["is_default"] for p in presets
    )

    blocks = [
        _name_block(),
        _block("p", "STATIC_TEXT", {"text": "{price} {currency}"}),
    ]
    created = op.post(
        f"{API}/admin/presets",
        json={
            "business_type_key": "general",
            "name": "Sales focus",
            "blocks": blocks,
            "is_default": False,
        },
    )
    assert created.status_code == 201, created.text
    pid = created.json()["preset_id"]

    # Propose a new DRAFT version.
    v2 = op.post(
        f"{API}/admin/presets/{pid}/versions",
        json={
            "blocks": [
                _name_block(),
                _block("x", "STATIC_TEXT", {"text": "new"}, ownership="STATIC"),
            ]
        },
    )
    assert v2.status_code == 201, v2.text
    assert v2.json()["status"] == "DRAFT"
    assert v2.json()["version"] == 2

    # Activate v2 -> v1 superseded.
    act = op.post(f"{API}/admin/presets/{pid}/versions/2/activate")
    assert act.status_code == 200, act.text
    assert act.json()["status"] == "ACTIVE"
    detail = next(p for p in op.get(f"{API}/admin/presets").json() if p["preset_id"] == pid)
    statuses = {v["version"]: v["status"] for v in detail["versions"]}
    assert statuses == {1: "SUPERSEDED", 2: "ACTIVE"}
    assert detail["active_version"] == 2

    # Rejecting an invalid token is a validation error, not a 500.
    bad = op.post(
        f"{API}/admin/presets",
        json={
            "business_type_key": "general",
            "name": "Bad preset",
            "blocks": [_block("a", "STATIC_TEXT", {"text": "{nope}"}, ownership="STATIC")],
        },
    )
    assert bad.status_code == 422


def test_admin_preset_requires_super_admin(db_client, db_session):
    _owner, _op, _bid = _bootstrap(db_client, db_session)
    # The business owner is NOT a platform operator.
    assert db_client.get(f"{API}/admin/presets").status_code == 403
    assert (
        db_client.post(
            f"{API}/admin/presets",
            json={
                "business_type_key": "general",
                "name": "X",
                "blocks": [_name_block()],
            },
        ).status_code
        == 403
    )


# --- business: view presets + preview ---


def test_business_views_own_business_type_presets(db_client, db_session):
    _owner, _op, bid = _bootstrap(db_client, db_session)
    presets = db_client.get(f"{API}/businesses/{bid}/presets").json()
    assert any(p["name"] == "Default preset" and p["is_default"] for p in presets)


def test_preview_default_preset_then_ai_integration(db_client, db_session):
    _owner, _op, bid = _bootstrap(db_client, db_session)
    pid = _seed_product(db_session, bid=bid)

    # Before AI: default preset renders product facts; AI block omitted.
    pre = db_client.get(f"{API}/businesses/{bid}/products/{pid}/preview").json()
    assert pre["source"] == "business_type_default"
    assert "Laptop X" in pre["text"]
    assert "1000" in pre["text"]
    assert pre["fits"] is True
    assert any("ai_description" in w for w in pre["warnings"])

    # Generate AI (deterministic template provider).
    before = _credits_remaining(db_session, bid=bid)
    gen = _generate(db_client, bid, pid)
    assert gen.status_code == 200, gen.text
    body = gen.json()
    assert body["reused"] is False
    assert body["artifact"]["status"] == "PENDING_APPROVAL"
    after = _credits_remaining(db_session, bid=bid)
    assert before - after == 1  # one credit consumed

    # PENDING (unapproved) AI is NOT rendered.
    mid = db_client.get(f"{API}/businesses/{bid}/products/{pid}/preview").json()
    assert body["artifact"]["generated_value"] not in mid["text"]

    # Approve -> now it renders.
    aid = body["artifact"]["artifact_id"]
    ap = db_client.post(f"{API}/businesses/{bid}/ai/artifacts/{aid}/approve")
    assert ap.status_code == 200, ap.text
    assert ap.json()["status"] == "APPROVED"
    post = db_client.get(f"{API}/businesses/{bid}/products/{pid}/preview").json()
    assert body["artifact"]["generated_value"] in post["text"]


# --- AI: reuse, regeneration, failure + refund ---


def test_ai_reuse_no_double_charge(db_client, db_session):
    _owner, _op, bid = _bootstrap(db_client, db_session)
    pid = _seed_product(db_session, bid=bid)
    first = _generate(db_client, bid, pid).json()
    assert first["reused"] is False
    # Spec: reuse applies to APPROVED artifacts (AI spec section 6).
    assert (
        db_client.post(
            f"{API}/businesses/{bid}/ai/artifacts/{first['artifact']['artifact_id']}/approve"
        ).status_code
        == 200
    )
    before = _credits_remaining(db_session, bid=bid)

    # Same inputs + same definition version -> reuse, no charge.
    second = _generate(db_client, bid, pid)
    assert second.status_code == 200, second.text
    assert second.json()["reused"] is True
    assert second.json()["artifact"]["artifact_id"] == first["artifact"]["artifact_id"]
    assert _credits_remaining(db_session, bid=bid) == before

    # An UNapproved artifact never masks a fresh generation.
    pid2 = _seed_product(db_session, bid=bid, name="Second product")
    g1 = _generate(db_client, bid, pid2).json()
    assert g1["reused"] is False
    g2 = _generate(db_client, bid, pid2).json()
    assert g2["reused"] is False
    assert g2["artifact"]["artifact_id"] != g1["artifact"]["artifact_id"]


def test_ai_regenerates_when_declared_inputs_change(db_client, db_session):
    _owner, _op, bid = _bootstrap(db_client, db_session)
    pid = _seed_product(db_session, bid=bid, name="Laptop X")
    first = _generate(db_client, bid, pid).json()
    assert first["reused"] is False

    # Change a declared input (name) -> new fingerprint -> new generation.
    product = db_session.get(Product, uuid.UUID(pid))
    product.name = "Laptop Y"
    db_session.commit()
    second = _generate(db_client, bid, pid).json()
    assert second["reused"] is False
    assert second["artifact"]["artifact_id"] != first["artifact"]["artifact_id"]
    assert "Laptop Y" in second["artifact"]["generated_value"]


def test_provider_transient_failure_refunds_credit(db_client, db_session, monkeypatch):
    import app.application.ai_generation as ai_gen

    monkeypatch.setattr(ai_gen, "_BACKOFF_BASE", 0)
    set_ai_provider_override(AlwaysTransient())
    try:
        _owner, _op, bid = _bootstrap(db_client, db_session)
        pid = _seed_product(db_session, bid=bid)
        before = _credits_remaining(db_session, bid=bid)
        resp = _generate(db_client, bid, pid)
        assert resp.status_code == 422, resp.text
        # No usable artifact -> the consumed credit was refunded.
        assert _credits_remaining(db_session, bid=bid) == before
    finally:
        set_ai_provider_override(None)


def test_invalid_output_refunds_after_bounded_attempts(db_client, db_session, monkeypatch):
    import app.application.ai_generation as ai_gen

    monkeypatch.setattr(ai_gen, "_BACKOFF_BASE", 0)
    set_ai_provider_override(InvalidLength())
    try:
        _owner, _op, bid = _bootstrap(db_client, db_session)
        pid = _seed_product(db_session, bid=bid)
        before = _credits_remaining(db_session, bid=bid)
        resp = _generate(db_client, bid, pid, key="ai_short_title")  # max 120
        assert resp.status_code == 422, resp.text
        assert _credits_remaining(db_session, bid=bid) == before
    finally:
        set_ai_provider_override(None)


def test_permanent_failure_no_retry_and_refund(db_client, db_session, monkeypatch):
    import app.application.ai_generation as ai_gen

    monkeypatch.setattr(ai_gen, "_BACKOFF_BASE", 0)
    set_ai_provider_override(Permanent())
    try:
        _owner, _op, bid = _bootstrap(db_client, db_session)
        pid = _seed_product(db_session, bid=bid)
        before = _credits_remaining(db_session, bid=bid)
        resp = _generate(db_client, bid, pid)
        assert resp.status_code == 422, resp.text
        assert _credits_remaining(db_session, bid=bid) == before
    finally:
        set_ai_provider_override(None)


# --- AI: edit / approve / reject / retry ---


def test_artifact_edit_approve_reject_lifecycle(db_client, db_session):
    _owner, _op, bid = _bootstrap(db_client, db_session)
    pid = _seed_product(db_session, bid=bid)
    aid = _generate(db_client, bid, pid).json()["artifact"]["artifact_id"]

    # Edit a PENDING artifact in place.
    edited = db_client.post(
        f"{API}/businesses/{bid}/ai/artifacts/{aid}", json={"text": "Edited copy"}
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["generated_value"] == "Edited copy"
    assert edited.json()["artifact_id"] == aid

    # Reject -> terminal for this artifact; no refund.
    rej = db_client.post(f"{API}/businesses/{bid}/ai/artifacts/{aid}/reject")
    assert rej.status_code == 200, rej.text
    assert rej.json()["status"] == "REJECTED"
    # Editing a REJECTED artifact is a conflict.
    assert (
        db_client.post(f"{API}/businesses/{bid}/ai/artifacts/{aid}", json={"text": "x"}).status_code
        == 409
    )

    # Retry produces a NEW artifact, then approve it.
    retried = db_client.post(
        f"{API}/businesses/{bid}/products/{pid}/ai/retry",
        json={"definition_key": "ai_description"},
    )
    assert retried.status_code == 200, retried.text
    new_aid = retried.json()["artifact"]["artifact_id"]
    assert new_aid != aid
    ap = db_client.post(f"{API}/businesses/{bid}/ai/artifacts/{new_aid}/approve")
    assert ap.status_code == 200, ap.text
    # Approving again is a conflict.
    assert (
        db_client.post(f"{API}/businesses/{bid}/ai/artifacts/{new_aid}/approve").status_code
        == 409
    )

    # Editing an APPROVED artifact creates a NEW pending (manual) artifact.
    again = db_client.post(
        f"{API}/businesses/{bid}/ai/artifacts/{new_aid}", json={"text": "Manual tweak"}
    )
    assert again.status_code == 200, again.text
    assert again.json()["artifact_id"] != new_aid
    assert again.json()["status"] == "PENDING_APPROVAL"
    assert again.json()["provider"] == "manual_edit"


def test_generate_blocked_when_plan_lacks_ai(db_client, db_session):
    _owner, op, _bid = _bootstrap(db_client, db_session)
    # Flip the starter plan off AI, then a new business on it.
    plans = op.get(f"{API}/plans").json()
    starter = next(p for p in plans if p["code"] == "starter")
    resp = op.patch(f"{API}/plans/{starter['plan_id']}", json={"ai_available": False})
    assert resp.status_code == 200, resp.text
    try:
        bid2 = db_client.post(
            f"{API}/businesses", json={"name": "NoAI", "business_type_key": "general"}
        ).json()["business_id"]
        sub = db_client.post(
            f"{API}/businesses/{bid2}/subscriptions", json={"plan_code": "starter"}
        ).json()
        op.post(
            f"{API}/subscriptions/{sub['subscription_id']}/payments/verify",
            json={"reference": "IR-NOAI"},
        )
        pid2 = _seed_product(db_session, bid=bid2)
        assert _generate(db_client, bid2, pid2).status_code == 403
    finally:
        op.patch(f"{API}/plans/{starter['plan_id']}", json={"ai_available": True})


def test_generate_blocked_when_no_credits(db_client, db_session):
    _owner, op, bid = _bootstrap(db_client, db_session)
    pid = _seed_product(db_session, bid=bid)
    plans = op.get(f"{API}/plans").json()
    starter = next(p for p in plans if p["code"] == "starter")
    op.patch(f"{API}/plans/{starter['plan_id']}", json={"ai_monthly_credits": 0})
    # Drains the existing pool to 0.
    for p in db_session.scalars(
        select(CreditPool).where(CreditPool.business_id == uuid.UUID(bid))
    ).all():
        p.remaining = 0
    db_session.commit()
    try:
        assert _generate(db_client, bid, pid).status_code == 409
    finally:
        op.patch(f"{API}/plans/{starter['plan_id']}", json={"ai_monthly_credits": 50})


# --- per-product presets: top-plan entitlement + non-destructive downgrade ---


def test_product_presets_top_plan_only_and_downgrade_safe(db_client, db_session):
    _owner, op, bid = _bootstrap(db_client, db_session)
    pid = _seed_product(db_session, bid=bid)

    # Starter is NOT top-plan -> creating a product preset is denied.
    denied = db_client.post(
        f"{API}/businesses/{bid}/product-presets",
        json={"name": "Premium", "blocks": [_name_block()]},
    )
    assert denied.status_code == 403, denied.text

    # Operator promotes the starter plan to top-plan.
    _set_plan_product_preset_eligible(op, code="starter", eligible=True)

    created = db_client.post(
        f"{API}/businesses/{bid}/product-presets",
        json={
            "name": "Premium",
            "blocks": [
                _name_block(),
                _block(
                    "banner",
                    "STATIC_TEXT",
                    {"text": "Premium banner"},
                    ownership="CUSTOMER_MANAGED",
                ),
            ],
        },
    )
    assert created.status_code == 201, created.text
    ppid = created.json()["product_preset_id"]

    # Assign it to the product.
    assign = db_client.post(
        f"{API}/businesses/{bid}/products/{pid}/preset",
        json={"product_preset_id": ppid},
    )
    assert assign.status_code == 200, assign.text
    assert assign.json()["product_preset_id"] == ppid

    # Preview now uses the product preset.
    pv = db_client.get(f"{API}/businesses/{bid}/products/{pid}/preview").json()
    assert pv["source"] == "product_preset"
    assert "Premium banner" in pv["text"]

    # Simulate a downgrade: plan no longer includes product presets.
    _set_plan_product_preset_eligible(op, code="starter", eligible=False)
    # The assignment is preserved (non-destructive) but ignored in render.
    product = db_session.get(Product, uuid.UUID(pid))
    assert product.product_preset_id == uuid.UUID(ppid)
    pv2 = db_client.get(f"{API}/businesses/{bid}/products/{pid}/preview").json()
    assert pv2["source"] == "business_type_default"
    assert "Premium banner" not in pv2["text"]
    assert any("product preset" in w for w in pv2["warnings"])

    # Clearing the assignment is always allowed (no entitlement needed).
    cleared = db_client.post(
        f"{API}/businesses/{bid}/products/{pid}/preset", json={"product_preset_id": None}
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["product_preset_id"] is None


# --- tenant isolation + permission narrowing ---


def test_cross_tenant_product_preview_404(db_client, db_session):
    _owner, _op, bid = _bootstrap(db_client, db_session)
    pid = _seed_product(db_session, bid=bid)
    # A second, unrelated business.
    other = _client_for(db_client)
    _register_login(other, "other@example.com", "Other")
    other.post(
        f"{API}/businesses", json={"name": "Other", "business_type_key": "general"}
    )
    # Trying to read the first business's product from the second context.
    resp = other.get(f"{API}/businesses/{bid}/products/{pid}/preview")
    assert resp.status_code == 404


def test_narrow_profile_cannot_generate_ai(db_client, db_session):
    owner, _op, bid = _bootstrap(db_client, db_session)
    pid = _seed_product(db_session, bid=bid)

    resp = owner.post(f"{API}/businesses/{bid}/invites", json={}).json()
    limited = _client_for(db_client)
    _register_login(limited, "limited@example.com", "Limited")
    req = limited.post(
        f"{API}/admin-requests", json={"method": "invite_code", "code": resp["code"]}
    ).json()
    approve = owner.post(
        f"{API}/businesses/{bid}/admin-requests/{req['request_id']}/approve"
    ).json()
    membership = db_session.get(Membership, uuid.UUID(approve["membership_id"]))
    membership.permissions = ["products.view", "ai.view"]
    db_session.commit()

    # Can view artifacts, but cannot generate.
    assert limited.get(f"{API}/businesses/{bid}/products/{pid}/ai").status_code == 200
    assert _generate(limited, bid, pid).status_code == 403


# --- automatic-mode toggle ---


def test_toggle_automatic_intent_persists(db_client, db_session):
    _owner, _op, bid = _bootstrap(db_client, db_session)
    on = db_client.post(f"{API}/businesses/{bid}/ai/automatic", json={"enabled": True})
    assert on.status_code == 200, on.text
    assert on.json()["ai_automatic_enabled"] is True
    off = db_client.post(f"{API}/businesses/{bid}/ai/automatic", json={"enabled": False})
    assert off.json()["ai_automatic_enabled"] is False


# --- definition versioning keeps historical artifacts ---


def test_definition_versioning_never_auto_regenerates(db_client, db_session):
    _owner, op, bid = _bootstrap(db_client, db_session)
    pid = _seed_product(db_session, bid=bid)
    gen = _generate(db_client, bid, pid).json()
    v1_aid = gen["artifact"]["artifact_id"]
    db_client.post(f"{API}/businesses/{bid}/ai/artifacts/{v1_aid}/approve")

    # Operator creates v2 of the same key (new prompt), activated.
    resp = op.post(
        f"{API}/admin/ai-definitions/ai_description/versions",
        json={"prompt_template": "New prompt v2 for {name}."},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["version"] == 2

    # The historical v1 artifact is untouched (not auto-regenerated).
    arts = db_client.get(f"{API}/businesses/{bid}/products/{pid}/ai").json()
    v1 = next(a for a in arts if a["artifact_id"] == v1_aid)
    assert v1["status"] == "APPROVED"
    assert v1["output_definition_version"] == 1

    # A fresh generation now uses v2 (new version on the new artifact).
    gen2 = _generate(db_client, bid, pid).json()
    assert gen2["definition_version"] == 2
    assert gen2["artifact"]["artifact_id"] != v1_aid
