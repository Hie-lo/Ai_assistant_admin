"""Business + membership/RBAC flow tests (full-stack through HTTP)."""

from __future__ import annotations

import uuid

import pytest
from app.domain import enums
from app.infrastructure.db.models import ChannelLink
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

PASSWORD = "correct-horse-battery-1"


def _register_login(client: TestClient, email: str, name: str = "T") -> None:
    assert (
        client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": PASSWORD, "display_name": name},
        ).status_code
        == 201
    )
    assert (
        client.post(
            "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
        ).status_code
        == 200
    )


def _create_business(client: TestClient, name: str = "Acme", type_key: str = "general") -> dict:
    resp = client.post("/api/v1/businesses", json={"name": name, "business_type_key": type_key})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_business_types_listed(db_client: TestClient) -> None:
    resp = db_client.get("/api/v1/business-types")
    assert resp.status_code == 200
    keys = [t["key"] for t in resp.json()]
    assert "general" in keys


def test_business_creation_gives_owner_membership(db_client: TestClient) -> None:
    _register_login(db_client, "owner@example.com", "Owner")
    b = _create_business(db_client, "Acme Corp")
    me = db_client.get("/api/v1/me").json()
    refs = me["businesses"]
    assert len(refs) == 1
    assert refs[0]["business_id"] == b["business_id"]
    assert refs[0]["role"] == "OWNER"


def test_unknown_business_type_rejected(db_client: TestClient) -> None:
    _register_login(db_client, "o2@example.com")
    resp = db_client.post(
        "/api/v1/businesses", json={"name": "X", "business_type_key": "does-not-exist"}
    )
    assert resp.status_code == 422


def test_admin_lifecycle_invite_approve_revoke(db_client: TestClient) -> None:
    # Owner on the primary client; candidate on a separate client.
    _register_login(db_client, "owner@example.com", "Owner")
    cand = _cand(db_client)
    _register_login(cand, "admin@example.com", "Admin")

    b = _create_business(db_client)
    bid = b["business_id"]

    # Owner creates an invite (admin.manage -> owner-only).
    code = db_client.post(f"/api/v1/businesses/{bid}/invites", json={}).json()["code"]

    # Candidate submits a request via the invite code.
    req = cand.post("/api/v1/admin-requests", json={"method": "invite_code", "code": code})
    assert req.status_code == 202, req.text
    body = req.json()
    assert body["created"] is True
    request_id = body["request_id"]

    # The single-use invite is consumed: re-using the same code fails...
    dup = cand.post("/api/v1/admin-requests", json={"method": "invite_code", "code": code})
    assert dup.status_code == 404
    # ...but a fresh invite for the same (business, candidate) coalesces.
    code2 = db_client.post(f"/api/v1/businesses/{bid}/invites", json={}).json()["code"]
    dup2 = cand.post("/api/v1/admin-requests", json={"method": "invite_code", "code": code2})
    assert dup2.status_code == 202
    assert dup2.json()["created"] is False
    assert dup2.json()["request_id"] == request_id

    # Owner sees the pending request (with candidate identity).
    pending = db_client.get(f"/api/v1/businesses/{bid}/admin-requests").json()
    assert len(pending) == 1
    assert pending[0]["candidate_email"] == "admin@example.com"

    # Owner approves -> ACTIVE ADMIN membership with the admin profile.
    approve = db_client.post(f"/api/v1/businesses/{bid}/admin-requests/{request_id}/approve")
    assert approve.status_code == 200, approve.text
    membership_id = approve.json()["membership_id"]
    assert approve.json()["role"] == "ADMIN"

    # Candidate now has business.view but NOT admin.manage (owner-only).
    members = cand.get(f"/api/v1/businesses/{bid}/members")
    assert members.status_code == 200
    assert {m["role"] for m in members.json()} == {"OWNER", "ADMIN"}
    forbidden = cand.post(f"/api/v1/businesses/{bid}/invites", json={})
    assert forbidden.status_code == 403

    # Owner revokes the admin -> membership REVOKED, sessions revoked.
    revoke = db_client.post(f"/api/v1/businesses/{bid}/memberships/{membership_id}/revoke")
    assert revoke.status_code == 200, revoke.text
    assert revoke.json()["status"] == "REVOKED"
    # The candidate's active sessions were revoked -> next call is 401.
    assert cand.get(f"/api/v1/businesses/{bid}/members").status_code == 401


def _new_app_for(client: TestClient):
    """Reuse the same app instance behind the fixture's TestClient."""
    return client.app


def test_self_approval_impossible(db_client: TestClient) -> None:
    _register_login(db_client, "owner@example.com")
    bid = _create_business(db_client)["business_id"]
    code = db_client.post(f"/api/v1/businesses/{bid}/invites", json={}).json()["code"]

    # Candidate is the owner themselves (own code, own business):
    # submitting is a no-op conflict (already a member), approving is blocked.
    resp = db_client.post(
        "/api/v1/admin-requests", json={"method": "invite_code", "code": code}
    )
    assert resp.status_code == 409  # already an active member
    # Even with a fabricated request id, the owner endpoint cannot be used
    # to self-grant: approval requires the request's candidate != approver.
    fake = uuid.uuid4()
    resp = db_client.post(f"/api/v1/businesses/{bid}/admin-requests/{fake}/approve")
    assert resp.status_code in (404, 409)


def test_cross_business_access_fails_closed(db_client: TestClient) -> None:
    _register_login(db_client, "a@example.com")
    b1 = _create_business(db_client, "Biz A")["business_id"]
    # A second owner in the same client is impossible (one cookie); use a
    # second client for the second business.
    b2_client = TestClient(_new_app_for(db_client))
    _register_login(b2_client, "b@example.com")
    b2 = _create_business(b2_client, "Biz B")["business_id"]

    # Owner A cannot read business B's members (404, not 403).
    assert db_client.get(f"/api/v1/businesses/{b2}/members").status_code == 404
    # Owner A cannot create an invite for business B.
    assert db_client.post(f"/api/v1/businesses/{b2}/invites", json={}).status_code == 404
    # Both businesses belong to their owners respectively.
    assert db_client.get("/api/v1/businesses").json()[0]["business_id"] == b1


def test_channel_ref_requires_linked_channel(
    db_client: TestClient, db_session: Session
) -> None:
    _register_login(db_client, "owner@example.com")
    bid = _create_business(db_client, "ChanCo")["business_id"]

    # Unknown channel: fail closed (404), no business info revealed.
    cand_client = _cand(db_client)
    _register_login(cand_client, "cand@example.com", "Cand")
    resp = cand_client.post(
        "/api/v1/admin-requests",
        json={"method": "channel_ref", "platform": "telegram", "channel_id": "nope"},
    )
    assert resp.status_code == 404

    # Link the channel to the business, then the same reference resolves.
    db_session.add(
        ChannelLink(
            business_id=uuid.UUID(bid),
            platform=enums.Platform.TELEGRAM.value,
            platform_target_id="chanco-official",
            status=enums.ChannelLinkStatus.LINKED.value,
            control_verified=False,
        )
    )
    db_session.commit()
    resp = cand_client.post(
        "/api/v1/admin-requests",
        json={"method": "channel_ref", "platform": "telegram", "channel_id": "chanco-official"},
    )
    assert resp.status_code == 202, resp.text
    assert resp.json()["created"] is True


def _cand(client: TestClient) -> TestClient:
    return TestClient(_new_app_for(client))


def test_reject_then_request_can_be_resubmitted(db_client: TestClient) -> None:
    _register_login(db_client, "owner@example.com")
    bid = _create_business(db_client)["business_id"]
    code = db_client.post(f"/api/v1/businesses/{bid}/invites", json={}).json()["code"]
    cand = _cand(db_client)
    _register_login(cand, "late@example.com", "Late")
    req = cand.post("/api/v1/admin-requests", json={"method": "invite_code", "code": code})
    assert req.status_code == 202
    rid = req.json()["request_id"]

    rej = db_client.post(f"/api/v1/businesses/{bid}/admin-requests/{rid}/reject")
    assert rej.status_code == 200
    assert rej.json()["status"] == "REJECTED"

    # After rejection, a new pending request may be created (new invite).
    code2 = db_client.post(f"/api/v1/businesses/{bid}/invites", json={}).json()["code"]
    req2 = cand.post("/api/v1/admin-requests", json={"method": "invite_code", "code": code2})
    assert req2.status_code == 202
    assert req2.json()["created"] is True
    assert req2.json()["request_id"] != rid


@pytest.mark.integration
def test_owner_cannot_revoke_own_ownership(db_client: TestClient) -> None:
    """Ownership transfer is a separate workflow; revoking the Owner 4xx."""
    _register_login(db_client, "owner@example.com")
    bid = _create_business(db_client)["business_id"]
    members = db_client.get(f"/api/v1/businesses/{bid}/members").json()
    owner_membership = next(m for m in members if m["role"] == "OWNER")
    resp = db_client.post(
        f"/api/v1/businesses/{bid}/memberships/{owner_membership['membership_id']}/revoke"
    )
    assert resp.status_code in (403, 404, 409)
