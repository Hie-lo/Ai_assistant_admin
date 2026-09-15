"""Phase 5 integration: Telegram connection + publication (manual V1).

Full HTTP stack with the fake in-memory Telegram client. Covers the
owner-approved V1 decisions:
- shared organization bot (no per-business token, never in responses);
- manual publish; albums capped at 10, caption <= 1024 with media;
- manual update with automatic NOOP / EDIT / REPOST classification;
- repost order: publish new -> verify -> delete old;
- remote manual delete is recorded, never auto-reposted;
- remote manual edit is flagged (remote_modified), never overwritten;
- permission loss / disconnect suspends, (re)verification resumes by
  reconciling — never bulk-republishing;
- tenant isolation and permission gates.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from app.domain import enums
from app.infrastructure.db.models import AuditLog, Post, Product, Publication
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.conftest import make_super_admin
from tests.fake_telegram import CANONICAL_CHAT_ID, FakeTelegramClient

pytestmark = pytest.mark.integration

PASSWORD = "correct-horse-battery-1"
API = "/api/v1"


# --- fixtures ---------------------------------------------------------------


@pytest.fixture()
def fake_tg() -> FakeTelegramClient:
    from app.infrastructure.platforms import telegram as tg

    fake = FakeTelegramClient()
    tg.set_telegram_client_override(fake)
    yield fake
    tg.set_telegram_client_override(None)


# --- helpers -----------------------------------------------------------------


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
        client.post(f"{API}/auth/login", json={"email": email, "password": PASSWORD}).status_code
        == 200
    )


def _bootstrap(db_client: TestClient, db_session: Session):
    """owner + super-admin operator + business on an ACTIVE starter plan."""
    _register_login(db_client, "owner@example.com", "Owner")
    make_super_admin(db_session, "operator@example.com")
    op = _client_for(db_client)
    assert (
        op.post(
            f"{API}/auth/login", json={"email": "operator@example.com", "password": PASSWORD}
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


def _seed_product(
    db_session: Session, *, bid: str, name: str = "Laptop X", description: str | None = None
) -> str:
    p = Product(
        business_id=uuid.UUID(bid),
        name=name,
        category="electronics",
        description=description if description is not None else "A fast laptop.",
        price=1000,
        currency="IRT",
        stock=3,
        attributes={"color": "black"},
    )
    db_session.add(p)
    db_session.commit()
    return str(p.product_id)


def _add_media(client: TestClient, bid: str, pid: str, urls: list[str]) -> None:
    for url in urls:
        resp = client.post(
            f"{API}/businesses/{bid}/products/{pid}/media",
            json={"url": url, "origin": "SOURCE"},
        )
        assert resp.status_code == 201, resp.text


def _connect(client: TestClient, bid: str, target: str = "@acme_channel") -> dict:
    resp = client.post(
        f"{API}/businesses/{bid}/connections",
        json={"platform": "TELEGRAM", "target": target},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _publish(client: TestClient, bid: str, pid: str, cid: str) -> httpx.Response:
    return client.post(
        f"{API}/businesses/{bid}/products/{pid}/publications",
        json={"connection_id": cid},
    )


# --- connection tests ---------------------------------------------------------


def test_connect_verified_normalizes_target_and_hides_token(db_client, db_session, fake_tg):
    _owner, _op, bid = _bootstrap(db_client, db_session)
    conn = _connect(db_client, bid, target="@acme_channel")

    assert conn["status"] == "VERIFIED"
    assert conn["control_verified"] is True
    assert conn["platform"] == "TELEGRAM"
    # The canonical chat id (not the entered username) becomes the target id.
    assert conn["platform_target_id"] == CANONICAL_CHAT_ID
    assert conn["target_name"] == "acme_channel"
    assert conn["last_verified_at"] is not None
    # No credential material in any response field.
    for key in ("token", "bot_token", "access_token", "secret"):
        assert key not in conn, key
    # Verification made exactly the three control calls.
    assert fake_tg.call_methods()[:3] == ["get_me", "get_chat", "get_chat_member"]
    # Audited.
    actions = [
        a.action
        for a in db_session.scalars(
            select(AuditLog).where(AuditLog.action == "connection.created")
        )
    ]
    assert "connection.created" in actions


def test_connect_bot_not_admin_becomes_permission_lost(db_client, db_session, fake_tg):
    fake_tg.is_admin = False
    _owner, _op, bid = _bootstrap(db_client, db_session)
    resp = db_client.post(
        f"{API}/businesses/{bid}/connections",
        json={"platform": "TELEGRAM", "target": "@acme_channel"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "PERMISSION_LOST"
    assert body["control_verified"] is False
    assert body["last_error_code"] == "PERMISSION_ERROR"


def test_connect_chat_not_found(db_client, db_session, fake_tg):
    fake_tg.chat_exists = False
    _owner, _op, bid = _bootstrap(db_client, db_session)
    resp = db_client.post(
        f"{API}/businesses/{bid}/connections",
        json={"platform": "TELEGRAM", "target": "@ghost_channel"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "PERMISSION_LOST"
    assert body["last_error_code"] == "NOT_FOUND"


def test_connect_shared_bot_unavailable_is_validation_error(db_client, db_session, fake_tg):
    fake_tg.token_ok = False
    _owner, _op, bid = _bootstrap(db_client, db_session)
    resp = db_client.post(
        f"{API}/businesses/{bid}/connections",
        json={"platform": "TELEGRAM", "target": "@acme_channel"},
    )
    assert resp.status_code == 422, resp.text
    # Nothing was created.
    assert db_client.get(f"{API}/businesses/{bid}/connections").json() == []


def test_connect_duplicate_target_conflict(db_client, db_session, fake_tg):
    _owner, _op, bid = _bootstrap(db_client, db_session)
    _connect(db_client, bid, target="@acme_channel")
    resp = db_client.post(
        f"{API}/businesses/{bid}/connections",
        json={"platform": "TELEGRAM", "target": "acme_channel"},  # same target, no @
    )
    assert resp.status_code == 409, resp.text


def test_connect_without_active_subscription_denied(db_client, db_session, fake_tg):
    _register_login(db_client, "owner2@example.com", "Owner2")
    bid = db_client.post(
        f"{API}/businesses", json={"name": "NoSub", "business_type_key": "general"}
    ).json()["business_id"]
    resp = db_client.post(
        f"{API}/businesses/{bid}/connections",
        json={"platform": "TELEGRAM", "target": "@nosub"},
    )
    assert resp.status_code == 403, resp.text


def test_channel_limit_starter_allows_two(db_client, db_session, fake_tg):
    _owner, _op, bid = _bootstrap(db_client, db_session)
    _connect(db_client, bid, target="@chan_one")
    _connect(db_client, bid, target="@chan_two")
    resp = db_client.post(
        f"{API}/businesses/{bid}/connections",
        json={"platform": "TELEGRAM", "target": "@chan_three"},
    )
    assert resp.status_code == 403, resp.text
    assert "channels" in resp.json()["error"]["message"]
    # A DISCONNECTED connection frees its slot.
    conns = db_client.get(f"{API}/businesses/{bid}/connections").json()
    first = next(c for c in conns if c["target_name"] == "chan_one")
    ok = db_client.post(f"{API}/businesses/{bid}/connections/{first['connection_id']}/disconnect")
    assert ok.status_code == 200
    third = db_client.post(
        f"{API}/businesses/{bid}/connections",
        json={"platform": "TELEGRAM", "target": "@chan_three"},
    )
    assert third.status_code == 201, third.text


def test_verify_reconnect_disconnect_flow(db_client, db_session, fake_tg):
    _owner, _op, bid = _bootstrap(db_client, db_session)
    conn = _connect(db_client, bid)
    cid = conn["connection_id"]

    # Re-verify a verified connection: still fine.
    assert db_client.post(f"{API}/businesses/{bid}/connections/{cid}/verify").status_code == 200

    # Disconnect: control proof cleared.
    off = db_client.post(f"{API}/businesses/{bid}/connections/{cid}/disconnect").json()
    assert off["status"] == "DISCONNECTED"
    assert off["control_verified"] is False

    # A disconnected connection cannot be verified directly...
    assert (
        db_client.post(f"{API}/businesses/{bid}/connections/{cid}/verify").status_code == 409
    )
    # ...only reconnected.
    back = db_client.post(f"{API}/businesses/{bid}/connections/{cid}/reconnect").json()
    assert back["status"] == "VERIFIED"
    assert back["control_verified"] is True


def test_connection_cross_tenant_404(db_client, db_session, fake_tg):
    owner1, _op, bid1 = _bootstrap(db_client, db_session)
    c2 = _client_for(db_client)
    _register_login(c2, "owner2@example.com", "Owner2")
    bid2 = c2.post(
        f"{API}/businesses", json={"name": "Other", "business_type_key": "general"}
    ).json()["business_id"]
    conn = _connect(owner1, bid1)
    cid = conn["connection_id"]

    assert c2.get(f"{API}/businesses/{bid2}/connections/{cid}").status_code == 404
    assert c2.get(f"{API}/businesses/{bid1}/connections").status_code == 404
    assert (
        c2.post(f"{API}/businesses/{bid1}/connections/{cid}/disconnect").status_code == 404
    )


def test_narrow_member_cannot_publish_or_connect(db_client, db_session, fake_tg):
    owner, _op, bid = _bootstrap(db_client, db_session)
    conn = _connect(owner, bid)
    pid = _seed_product(db_session, bid=bid)

    from app.infrastructure.db.models import Membership

    limited = _client_for(db_client)
    _register_login(limited, "limited@example.com", "Limited")
    resp = owner.post(f"{API}/businesses/{bid}/invites", json={}).json()
    req = limited.post(
        "/api/v1/admin-requests", json={"method": "invite_code", "code": resp["code"]}
    ).json()
    approve = owner.post(
        f"{API}/businesses/{bid}/admin-requests/{req['request_id']}/approve"
    ).json()
    membership = db_session.get(Membership, uuid.UUID(approve["membership_id"]))
    membership.permissions = ["posts.view", "products.view"]
    db_session.commit()

    # view is allowed
    assert limited.get(f"{API}/businesses/{bid}/publications").status_code == 200
    # publish / repost / delete / update / connection management denied
    r = _publish(limited, bid, pid, conn["connection_id"])
    assert r.status_code == 403
    conns = limited.get(f"{API}/businesses/{bid}/connections")
    assert conns.status_code == 403
    assert (
        limited.post(
            f"{API}/businesses/{bid}/connections/{conn['connection_id']}/verify"
        ).status_code
        == 403
    )


# --- publish tests -----------------------------------------------------------


def test_publish_success_records_remote_and_attempt(db_client, db_session, fake_tg):
    owner, _op, bid = _bootstrap(db_client, db_session)
    conn = _connect(owner, bid)
    pid = _seed_product(db_session, bid=bid)

    resp = _publish(owner, bid, pid, conn["connection_id"])
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["created"] is True
    pub = body["publication"]
    assert pub["status"] == "PUBLISHED"
    assert pub["remote_message_id"] is not None
    assert pub["text"]
    assert pub["media_urls"] == []

    # Exactly one remote message, with the rendered text.
    mid = int(pub["remote_message_id"])
    assert fake_tg.remote_text(mid) == pub["text"]
    assert len(fake_tg.remote) == 1

    # Attempt recorded; audited.
    pub_id = uuid.UUID(pub["publication_id"])
    attempts = db_session.scalars(
        select(Publication).where(Publication.publication_id == pub_id)
    )
    assert list(attempts)[0].status == "PUBLISHED"
    actions = [
        a.action
        for a in db_session.scalars(
            select(AuditLog).where(AuditLog.action == "publication.published")
        )
    ]
    assert "publication.published" in actions


def test_publish_duplicate_blocked_effectively_once(db_client, db_session, fake_tg):
    owner, _op, bid = _bootstrap(db_client, db_session)
    conn = _connect(owner, bid)
    pid = _seed_product(db_session, bid=bid)

    first = _publish(owner, bid, pid, conn["connection_id"]).json()
    second = _publish(owner, bid, pid, conn["connection_id"]).json()

    assert first["created"] is True
    assert second["created"] is False
    assert second["publication"]["publication_id"] == first["publication"]["publication_id"]
    # No second remote message was sent.
    assert len(fake_tg.remote) == 1


def test_publish_media_album_capped_at_ten(db_client, db_session, fake_tg):
    owner, _op, bid = _bootstrap(db_client, db_session)
    conn = _connect(owner, bid)
    pid = _seed_product(db_session, bid=bid)
    urls = [f"https://cdn.example.com/img{i}.jpg" for i in range(12)]
    _add_media(owner, bid, pid, urls)

    resp = _publish(owner, bid, pid, conn["connection_id"])
    assert resp.status_code == 201, resp.text
    pub = resp.json()["publication"]
    assert pub["status"] == "PUBLISHED"
    mid = int(pub["remote_message_id"])
    entry = fake_tg.remote[(CANONICAL_CHAT_ID, mid)]
    # 12 eligible -> capped at the album limit of 10, first ten in order.
    assert entry["media"] == urls[:10]
    # With media the text is a CAPTION.
    assert "caption" in entry
    assert len(entry["caption"]) <= 1024


def test_publish_caption_overflow_trims_to_fit(db_client, db_session, fake_tg):
    owner, _op, bid = _bootstrap(db_client, db_session)
    conn = _connect(owner, bid)
    # Description far beyond the 1024 caption limit.
    pid = _seed_product(db_session, bid=bid, description="خ" * 3000)
    _add_media(owner, bid, pid, ["https://cdn.example.com/photo.jpg"])

    resp = _publish(owner, bid, pid, conn["connection_id"])
    assert resp.status_code == 201, resp.text
    pub = resp.json()["publication"]
    mid = int(pub["remote_message_id"])
    entry = fake_tg.remote[(CANONICAL_CHAT_ID, mid)]
    caption = entry["caption"]
    assert len(caption) <= 1024
    # Essentials survive the trim: the product name is still present.
    assert "Laptop X" in caption
    # The remote caption and the reported text agree.
    assert caption == pub["text"]


def test_publish_timeout_becomes_unknown_and_check_unresolved(db_client, db_session, fake_tg):
    owner, _op, bid = _bootstrap(db_client, db_session)
    conn = _connect(owner, bid)
    pid = _seed_product(db_session, bid=bid)
    fake_tg.fail_next_call("send_message", enums.PublicationErrorCode.NETWORK_TIMEOUT)

    resp = _publish(owner, bid, pid, conn["connection_id"])
    assert resp.status_code == 201, resp.text
    pub = resp.json()["publication"]
    # A timeout is NEVER classified as failed.
    assert pub["status"] == "UNKNOWN_REMOTE_STATE"
    assert pub["error_code"] == "NETWORK_TIMEOUT"
    assert pub["remote_message_id"] is None

    # Reconciliation without a remote id stays explicitly unresolved.
    check = db_client.post(f"{API}/businesses/{bid}/publications/{pub['publication_id']}/check")
    assert check.status_code == 200, check.text
    body = check.json()
    assert body["publication"]["status"] == "UNKNOWN_REMOTE_STATE"
    assert body["finding"] == "unresolved_no_remote_id"

    # The duplicate guard still holds: no blind re-publish.
    again = _publish(owner, bid, pid, conn["connection_id"]).json()
    assert again["created"] is False


def test_publish_rate_limited_is_retryable(db_client, db_session, fake_tg):
    owner, _op, bid = _bootstrap(db_client, db_session)
    conn = _connect(owner, bid)
    pid = _seed_product(db_session, bid=bid)
    fake_tg.fail_next_call("send_message", enums.PublicationErrorCode.RATE_LIMITED)

    resp = _publish(owner, bid, pid, conn["connection_id"])
    assert resp.status_code == 201, resp.text
    pub = resp.json()["publication"]
    assert pub["status"] == "FAILED_RETRYABLE"
    assert pub["error_code"] == "RATE_LIMITED"


def test_publish_permission_error_is_final(db_client, db_session, fake_tg):
    owner, _op, bid = _bootstrap(db_client, db_session)
    conn = _connect(owner, bid)
    pid = _seed_product(db_session, bid=bid)
    fake_tg.fail_next_call("send_message", enums.PublicationErrorCode.PERMISSION_ERROR)

    resp = _publish(owner, bid, pid, conn["connection_id"])
    assert resp.status_code == 201, resp.text
    pub = resp.json()["publication"]
    assert pub["status"] == "FAILED_FINAL"
    assert pub["error_code"] == "PERMISSION_ERROR"


def test_publish_archived_product_blocked(db_client, db_session, fake_tg):
    owner, _op, bid = _bootstrap(db_client, db_session)
    conn = _connect(owner, bid)
    pid = _seed_product(db_session, bid=bid)
    assert owner.post(f"{API}/businesses/{bid}/products/{pid}/archive").status_code == 200

    resp = _publish(owner, bid, pid, conn["connection_id"])
    assert resp.status_code == 409, resp.text


def test_publish_requires_verified_connection(db_client, db_session, fake_tg):
    fake_tg.is_admin = False
    owner, _op, bid = _bootstrap(db_client, db_session)
    conn = _connect(owner, bid)  # created but PERMISSION_LOST
    pid = _seed_product(db_session, bid=bid)
    resp = _publish(owner, bid, pid, conn["connection_id"])
    assert resp.status_code == 409, resp.text
    fake_tg.is_admin = True
    # After re-verification the same publish succeeds.
    assert (
        owner.post(
            f"{API}/businesses/{bid}/connections/{conn['connection_id']}/verify"
        ).status_code
        == 200
    )
    ok = _publish(owner, bid, pid, conn["connection_id"])
    assert ok.status_code == 201, ok.text


# --- update / repost / delete tests -------------------------------------------


def test_update_noop_when_unchanged(db_client, db_session, fake_tg):
    owner, _op, bid = _bootstrap(db_client, db_session)
    conn = _connect(owner, bid)
    pid = _seed_product(db_session, bid=bid)
    pub = _publish(owner, bid, pid, conn["connection_id"]).json()["publication"]

    resp = db_client.post(f"{API}/businesses/{bid}/publications/{pub['publication_id']}/update")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["plan"] == "NOOP"
    assert body["updated"] is False
    assert body["publication"]["status"] == "PUBLISHED"
    # No edit call was made.
    assert "edit_message_text" not in fake_tg.call_methods()
    assert "edit_message_caption" not in fake_tg.call_methods()


def test_update_edit_updates_remote_text(db_client, db_session, fake_tg):
    owner, _op, bid = _bootstrap(db_client, db_session)
    conn = _connect(owner, bid)
    pid = _seed_product(db_session, bid=bid, name="Laptop X")
    pub = _publish(owner, bid, pid, conn["connection_id"]).json()["publication"]
    mid = int(pub["remote_message_id"])

    # Change the product name (text change, no media) -> EDIT.
    r = owner.patch(f"{API}/businesses/{bid}/products/{pid}", json={"name": "Laptop Pro"})
    assert r.status_code == 200, r.text
    resp = db_client.post(f"{API}/businesses/{bid}/publications/{pub['publication_id']}/update")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["plan"] == "EDIT"
    assert body["updated"] is True
    assert body["publication"]["status"] == "PUBLISHED"
    assert body["publication"]["remote_message_id"] == pub["remote_message_id"]
    # Same remote message, updated content.
    assert len(fake_tg.remote) == 1
    assert "Laptop Pro" in fake_tg.remote_text(mid)
    assert "edit_message_text" in fake_tg.call_methods()


def test_update_media_change_reposts_new_then_deletes_old(db_client, db_session, fake_tg):
    owner, _op, bid = _bootstrap(db_client, db_session)
    conn = _connect(owner, bid)
    pid = _seed_product(db_session, bid=bid)
    _add_media(owner, bid, pid, ["https://cdn.example.com/a.jpg", "https://cdn.example.com/b.jpg"])
    pub = _publish(owner, bid, pid, conn["connection_id"]).json()["publication"]
    old_mid = int(pub["remote_message_id"])
    assert len(fake_tg.remote) == 1

    # Media change (add a third photo) -> REPOST.
    _add_media(owner, bid, pid, ["https://cdn.example.com/c.jpg"])
    resp = db_client.post(f"{API}/businesses/{bid}/publications/{pub['publication_id']}/update")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["plan"] == "REPOST"
    assert body["updated"] is True

    old = body["publication"]
    new = body["new_publication"]
    assert old["status"] == "REMOTE_DELETED"
    assert new["status"] == "PUBLISHED"
    assert new["remote_message_id"] != old_mid

    # Remote: old gone, new live with the full media set.
    assert (CANONICAL_CHAT_ID, old_mid) not in fake_tg.remote
    new_mid = int(new["remote_message_id"])
    entry = fake_tg.remote[(CANONICAL_CHAT_ID, new_mid)]
    assert entry["media"] == [
        "https://cdn.example.com/a.jpg",
        "https://cdn.example.com/b.jpg",
        "https://cdn.example.com/c.jpg",
    ]
    # Remote order: send new -> verify -> delete old.
    methods = fake_tg.call_methods()
    assert methods.index("send_media_group") < methods.index("delete_message")
    assert methods.index("get_message") < methods.index("delete_message")


def test_forced_repost_creates_new_remote_message(db_client, db_session, fake_tg):
    owner, _op, bid = _bootstrap(db_client, db_session)
    conn = _connect(owner, bid)
    pid = _seed_product(db_session, bid=bid)
    pub = _publish(owner, bid, pid, conn["connection_id"]).json()["publication"]
    old_mid = int(pub["remote_message_id"])

    resp = db_client.post(f"{API}/businesses/{bid}/publications/{pub['publication_id']}/repost")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["updated"] is True
    assert body["publication"]["status"] == "REMOTE_DELETED"
    new = body["new_publication"]
    assert new["status"] == "PUBLISHED"
    assert (CANONICAL_CHAT_ID, old_mid) not in fake_tg.remote
    assert int(new["remote_message_id"]) != old_mid
    assert len(fake_tg.remote) == 1


def test_repost_verification_failure_then_reconcile_completes(db_client, db_session, fake_tg):
    owner, _op, bid = _bootstrap(db_client, db_session)
    conn = _connect(owner, bid)
    pid = _seed_product(db_session, bid=bid)
    _add_media(owner, bid, pid, ["https://cdn.example.com/a.jpg"])
    pub = _publish(owner, bid, pid, conn["connection_id"]).json()["publication"]
    old_mid = int(pub["remote_message_id"])

    # Media change -> REPOST; the NEW send succeeds but verification fails.
    _add_media(owner, bid, pid, ["https://cdn.example.com/b.jpg"])
    fake_tg.fail_next_call("get_message", enums.PublicationErrorCode.NETWORK_ERROR)
    resp = db_client.post(f"{API}/businesses/{bid}/publications/{pub['publication_id']}/update")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    old = body["publication"]
    new = body["new_publication"]
    # Old untouched (still live) — the owner-approved safety rule.
    assert old["status"] == "PUBLISHED"
    assert new["status"] == "UNKNOWN_REMOTE_STATE"
    assert new["remote_message_id"] is not None
    assert len(fake_tg.remote) == 2  # both messages exist remotely

    # Reconciling the new one completes the interrupted repost:
    # the old remote message is deleted, states converge.
    check = db_client.post(
        f"{API}/businesses/{bid}/publications/{new['publication_id']}/check"
    )
    assert check.status_code == 200, check.text
    after = check.json()
    assert after["publication"]["status"] == "PUBLISHED"
    assert after["finding"] == "published"
    assert (CANONICAL_CHAT_ID, old_mid) not in fake_tg.remote
    old_row = db_session.get(Publication, uuid.UUID(old["publication_id"]))
    assert old_row.status == "REMOTE_DELETED"
    assert len(fake_tg.remote) == 1


def test_remote_manual_delete_recorded_never_auto_reposted(db_client, db_session, fake_tg):
    owner, _op, bid = _bootstrap(db_client, db_session)
    conn = _connect(owner, bid)
    pid = _seed_product(db_session, bid=bid)
    pub = _publish(owner, bid, pid, conn["connection_id"]).json()["publication"]
    mid = int(pub["remote_message_id"])

    # The owner deletes the message directly in Telegram.
    del fake_tg.remote[(CANONICAL_CHAT_ID, mid)]

    check = db_client.post(f"{API}/businesses/{bid}/publications/{pub['publication_id']}/check")
    assert check.status_code == 200, check.text
    body = check.json()
    assert body["publication"]["status"] == "REMOTE_DELETED"
    assert body["finding"] == "remote_deleted"
    # V1 policy: NEVER auto-republish.
    assert len(fake_tg.remote) == 0
    post = db_session.scalars(
        select(Post).where(Post.product_id == uuid.UUID(pid))
    ).one()
    assert post.status == "ARCHIVED"

    # A fresh publish is allowed again (new intent, new slot).
    again = _publish(owner, bid, pid, conn["connection_id"])
    assert again.status_code == 201, again.text
    assert again.json()["created"] is True


def test_remote_manual_edit_flagged_and_never_overwritten(db_client, db_session, fake_tg):
    owner, _op, bid = _bootstrap(db_client, db_session)
    conn = _connect(owner, bid)
    pid = _seed_product(db_session, bid=bid)
    pub = _publish(owner, bid, pid, conn["connection_id"]).json()["publication"]
    mid = int(pub["remote_message_id"])

    # Manual edit directly in Telegram.
    fake_tg.remote[(CANONICAL_CHAT_ID, mid)]["text"] = "hand-edited by the owner"

    check = db_client.post(f"{API}/businesses/{bid}/publications/{pub['publication_id']}/check")
    assert check.status_code == 200, check.text
    body = check.json()
    # Still PUBLISHED but flagged as remotely modified...
    assert body["publication"]["status"] == "PUBLISHED"
    assert body["publication"]["remote_modified"] is True
    assert body["finding"] == "published"
    # ...and the system does NOT overwrite the manual edit.
    assert fake_tg.remote_text(mid) == "hand-edited by the owner"
    assert "edit_message_text" not in fake_tg.call_methods()


def test_delete_publication_removes_remote(db_client, db_session, fake_tg):
    owner, _op, bid = _bootstrap(db_client, db_session)
    conn = _connect(owner, bid)
    pid = _seed_product(db_session, bid=bid)
    pub = _publish(owner, bid, pid, conn["connection_id"]).json()["publication"]
    mid = int(pub["remote_message_id"])

    resp = db_client.post(f"{API}/businesses/{bid}/publications/{pub['publication_id']}/delete")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deleted"] is True
    assert body["publication"]["status"] == "REMOTE_DELETED"
    assert (CANONICAL_CHAT_ID, mid) not in fake_tg.remote
    # Deleting again is a conflict (no remote message left).
    assert (
        db_client.post(f"{API}/businesses/{bid}/publications/{pub['publication_id']}/delete")
        .status_code
        == 409
    )


def test_permission_loss_suspends_and_verify_resumes(db_client, db_session, fake_tg):
    owner, _op, bid = _bootstrap(db_client, db_session)
    conn = _connect(owner, bid)
    pid = _seed_product(db_session, bid=bid)
    pub = _publish(owner, bid, pid, conn["connection_id"]).json()["publication"]

    # The bot loses admin rights in the channel.
    fake_tg.is_admin = False
    v = owner.post(f"{API}/businesses/{bid}/connections/{conn['connection_id']}/verify")
    assert v.status_code == 200, v.text
    assert v.json()["status"] == "PERMISSION_LOST"
    row = db_session.get(Publication, uuid.UUID(pub["publication_id"]))
    assert row.status == "PERMISSION_LOST"

    # Rights restored -> re-verify resumes by reconciling (no republish).
    fake_tg.is_admin = True
    v2 = owner.post(f"{API}/businesses/{bid}/connections/{conn['connection_id']}/verify")
    assert v2.status_code == 200, v2.text
    assert v2.json()["status"] == "VERIFIED"
    db_session.expire_all()
    row = db_session.get(Publication, uuid.UUID(pub["publication_id"]))
    assert row.status == "PUBLISHED"
    # No new remote message was created (resume, not republish).
    assert len(fake_tg.remote) == 1


def test_disconnect_suspends_and_reconnect_resumes(db_client, db_session, fake_tg):
    owner, _op, bid = _bootstrap(db_client, db_session)
    conn = _connect(owner, bid)
    pid = _seed_product(db_session, bid=bid)
    pub = _publish(owner, bid, pid, conn["connection_id"]).json()["publication"]

    off = owner.post(f"{API}/businesses/{bid}/connections/{conn['connection_id']}/disconnect")
    assert off.status_code == 200
    row = db_session.get(Publication, uuid.UUID(pub["publication_id"]))
    assert row.status == "DISCONNECTED"
    # Disconnecting never deletes remote content.
    assert len(fake_tg.remote) == 1

    back = owner.post(f"{API}/businesses/{bid}/connections/{conn['connection_id']}/reconnect")
    assert back.status_code == 200
    assert back.json()["status"] == "VERIFIED"
    db_session.expire_all()
    row = db_session.get(Publication, uuid.UUID(pub["publication_id"]))
    assert row.status == "PUBLISHED"
    assert len(fake_tg.remote) == 1


def test_publication_cross_tenant_404(db_client, db_session, fake_tg):
    owner1, _op, bid1 = _bootstrap(db_client, db_session)
    conn = _connect(owner1, bid1)
    pid = _seed_product(db_session, bid=bid1)
    pub = _publish(owner1, bid1, pid, conn["connection_id"]).json()["publication"]

    c2 = _client_for(db_client)
    _register_login(c2, "owner2@example.com", "Owner2")
    bid2 = c2.post(
        f"{API}/businesses", json={"name": "Other", "business_type_key": "general"}
    ).json()["business_id"]

    assert c2.get(f"{API}/businesses/{bid2}/publications").json() == []
    assert c2.get(f"{API}/businesses/{bid1}/publications").status_code == 404
    assert (
        c2.post(f"{API}/businesses/{bid1}/publications/{pub['publication_id']}/delete").status_code
        == 404
    )
