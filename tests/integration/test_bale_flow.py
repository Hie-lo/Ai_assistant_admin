"""Phase 6 integration: Bale connection + publication (manual V1).

Full HTTP stack with the fake in-memory Bale client (tests/fake_bale.py).
Covers the owner-approved Phase 6 decisions (2026-09-15):

- shared organization BALE bot (same model as Telegram; token never leaks);
- albums capped at 10; single-media caption <= 4096, album caption <= 1024;
- markdown escaping on the wire (Bale always markdown-parses text);
- the 48h delete limit: a failed OLD-delete during a repost leaves the new
  message live and the old one TRACKED (FAILED_FINAL, remote id kept) for
  manual owner deletion — never silently lost;
- Bale has NO message lookup: check/reconciliation is an explicit
  conflict (no silent fallback), suspended publications resume from
  (re)verification without pretending to inspect.
"""

from __future__ import annotations

import uuid

import pytest
from app.domain import enums
from app.infrastructure.db.models import Publication
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.conftest import make_super_admin
from tests.fake_bale import CANONICAL_CHAT_ID, FakeBaleClient

pytestmark = pytest.mark.integration

PASSWORD = "correct-horse-battery-1"
API = "/api/v1"


# --- fixtures ---------------------------------------------------------------


@pytest.fixture()
def fake_bale() -> FakeBaleClient:
    from app.infrastructure.platforms import bale

    fake = FakeBaleClient()
    bale.set_bale_client_override(fake)
    yield fake
    bale.set_bale_client_override(None)


# --- helpers ------------------------------------------------------------------


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
    from app.infrastructure.db.models import Product

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
        json={"platform": "BALE", "target": target},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _publish(client: TestClient, bid: str, pid: str, cid: str):
    return client.post(
        f"{API}/businesses/{bid}/products/{pid}/publications",
        json={"connection_id": cid},
    )


# --- connection tests ---------------------------------------------------------


def test_connect_bale_verified_normalizes_target_and_hides_token(
    db_client, db_session, fake_bale
):
    _owner, _op, bid = _bootstrap(db_client, db_session)
    conn = _connect(db_client, bid, target="@acme_channel")

    assert conn["status"] == "VERIFIED"
    assert conn["control_verified"] is True
    assert conn["platform"] == "BALE"
    assert conn["platform_target_id"] == CANONICAL_CHAT_ID
    assert conn["target_name"] == "acme_channel"
    assert conn["last_verified_at"] is not None
    for key in ("token", "bot_token", "access_token", "secret"):
        assert key not in conn, key
    # Verification made exactly the three control calls (same surface).
    assert fake_bale.call_methods()[:3] == ["get_me", "get_chat", "get_chat_member"]


def test_connect_bale_bot_not_admin_becomes_permission_lost(db_client, db_session, fake_bale):
    fake_bale.is_admin = False
    _owner, _op, bid = _bootstrap(db_client, db_session)
    resp = db_client.post(
        f"{API}/businesses/{bid}/connections",
        json={"platform": "BALE", "target": "@acme_channel"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "PERMISSION_LOST"
    assert body["control_verified"] is False
    assert body["last_error_code"] == "PERMISSION_ERROR"


def test_connect_bale_chat_not_found(db_client, db_session, fake_bale):
    fake_bale.chat_exists = False
    _owner, _op, bid = _bootstrap(db_client, db_session)
    resp = db_client.post(
        f"{API}/businesses/{bid}/connections",
        json={"platform": "BALE", "target": "@ghost_channel"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "PERMISSION_LOST"
    assert body["last_error_code"] == "NOT_FOUND"


def test_connect_bale_shared_bot_unavailable_is_validation_error(
    db_client, db_session, fake_bale
):
    fake_bale.token_ok = False
    _owner, _op, bid = _bootstrap(db_client, db_session)
    resp = db_client.post(
        f"{API}/businesses/{bid}/connections",
        json={"platform": "BALE", "target": "@acme_channel"},
    )
    assert resp.status_code == 422, resp.text
    assert db_client.get(f"{API}/businesses/{bid}/connections").json() == []


def test_connect_unsupported_platform_rejected(db_client, db_session, fake_bale):
    _owner, _op, bid = _bootstrap(db_client, db_session)
    resp = db_client.post(
        f"{API}/businesses/{bid}/connections",
        json={"platform": "EITAA", "target": "@chan"},
    )
    assert resp.status_code == 422, resp.text
    assert db_client.get(f"{API}/businesses/{bid}/connections").json() == []


# --- publish tests -----------------------------------------------------------


def test_publish_bale_text_stores_escaped_on_wire(db_client, db_session, fake_bale):
    owner, _op, bid = _bootstrap(db_client, db_session)
    conn = _connect(owner, bid)
    # The product text contains markdown specials that Bale would
    # otherwise re-interpret as bold/link markup.
    pid = _seed_product(
        db_session, bid=bid, description="Fast *gaming* laptop (RTX) [new] _2026_"
    )

    resp = _publish(owner, bid, pid, conn["connection_id"])
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["created"] is True
    pub = body["publication"]
    assert pub["status"] == "PUBLISHED"
    assert pub["remote_message_id"] is not None

    # The remote stores the ESCAPED wire form — the published text is
    # exactly what the owner approved, nothing more.
    from app.infrastructure.platforms.bale import escape_markdown

    mid = int(pub["remote_message_id"])
    assert fake_bale.remote_text(mid) == escape_markdown(pub["text"])
    assert len(fake_bale.remote) == 1


def test_publish_bale_album_capped_at_ten(db_client, db_session, fake_bale):
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
    entry = fake_bale.remote[(CANONICAL_CHAT_ID, mid)]
    assert entry["media"] == urls[:10]  # capped at the album limit
    assert "caption" in entry
    assert len(entry["caption"]) <= 1024


def test_publish_bale_single_media_caption_limit_4096(db_client, db_session, fake_bale):
    owner, _op, bid = _bootstrap(db_client, db_session)
    conn = _connect(owner, bid)
    # 3000 chars: over Telegram's 1024 caption limit but under Bale's
    # single-media 4096 — Bale publishes it UNTRIMMED.
    pid = _seed_product(db_session, bid=bid, description="خ" * 3000)
    _add_media(owner, bid, pid, ["https://cdn.example.com/photo.jpg"])

    resp = _publish(owner, bid, pid, conn["connection_id"])
    assert resp.status_code == 201, resp.text
    pub = resp.json()["publication"]
    mid = int(pub["remote_message_id"])
    entry = fake_bale.remote[(CANONICAL_CHAT_ID, mid)]
    assert len(entry["caption"]) <= 4096
    assert "Laptop X" in entry["caption"]
    # Not trimmed: the full 3000-char description is present.
    assert entry["caption"].count("خ") == 3000


def test_publish_bale_album_caption_trims_to_1024(db_client, db_session, fake_bale):
    owner, _op, bid = _bootstrap(db_client, db_session)
    conn = _connect(owner, bid)
    pid = _seed_product(db_session, bid=bid, description="خ" * 3000)
    _add_media(owner, bid, pid, ["https://cdn.example.com/a.jpg", "https://cdn.example.com/b.jpg"])

    resp = _publish(owner, bid, pid, conn["connection_id"])
    assert resp.status_code == 201, resp.text
    pub = resp.json()["publication"]
    mid = int(pub["remote_message_id"])
    entry = fake_bale.remote[(CANONICAL_CHAT_ID, mid)]
    # Album caption limit (1024) applies — trimmed by priority, blocked
    # if essentials don't fit; the product name survives.
    assert len(entry["caption"]) <= 1024
    assert "Laptop X" in entry["caption"]
    assert entry["caption"] == pub["text"]


def test_publish_bale_timeout_is_unknown_and_check_is_explicit(
    db_client, db_session, fake_bale
):
    owner, _op, bid = _bootstrap(db_client, db_session)
    conn = _connect(owner, bid)
    pid = _seed_product(db_session, bid=bid)
    fake_bale.fail_next_call("send_message", enums.PublicationErrorCode.NETWORK_TIMEOUT)

    resp = _publish(owner, bid, pid, conn["connection_id"])
    assert resp.status_code == 201, resp.text
    pub = resp.json()["publication"]
    assert pub["status"] == "UNKNOWN_REMOTE_STATE"
    assert pub["error_code"] == "NETWORK_TIMEOUT"
    assert pub["remote_message_id"] is None

    # No message lookup exists on Bale: the check is an explicit conflict,
    # never a silent guess (adapter spec section 8).
    check = db_client.post(
        f"{API}/businesses/{bid}/publications/{pub['publication_id']}/check"
    )
    assert check.status_code == 409, check.text
    assert "remote inspection is not supported" in check.json()["error"]["message"]

    # The duplicate guard still holds: no blind re-publish.
    again = _publish(owner, bid, pid, conn["connection_id"]).json()
    assert again["created"] is False


def test_publish_bale_rate_limited_is_retryable(db_client, db_session, fake_bale):
    owner, _op, bid = _bootstrap(db_client, db_session)
    conn = _connect(owner, bid)
    pid = _seed_product(db_session, bid=bid)
    fake_bale.fail_next_call("send_message", enums.PublicationErrorCode.RATE_LIMITED)

    resp = _publish(owner, bid, pid, conn["connection_id"])
    assert resp.status_code == 201, resp.text
    pub = resp.json()["publication"]
    assert pub["status"] == "FAILED_RETRYABLE"
    assert pub["error_code"] == "RATE_LIMITED"


def test_publish_bale_requires_verified_connection(db_client, db_session, fake_bale):
    fake_bale.is_admin = False
    owner, _op, bid = _bootstrap(db_client, db_session)
    conn = _connect(owner, bid)  # created but PERMISSION_LOST
    pid = _seed_product(db_session, bid=bid)
    resp = _publish(owner, bid, pid, conn["connection_id"])
    assert resp.status_code == 409, resp.text
    fake_bale.is_admin = True
    assert (
        owner.post(
            f"{API}/businesses/{bid}/connections/{conn['connection_id']}/verify"
        ).status_code
        == 200
    )
    ok = _publish(owner, bid, pid, conn["connection_id"])
    assert ok.status_code == 201, ok.text


# --- update / repost / 48h tests ---------------------------------------------


def test_update_bale_edit_updates_remote_text(db_client, db_session, fake_bale):
    owner, _op, bid = _bootstrap(db_client, db_session)
    conn = _connect(owner, bid)
    pid = _seed_product(db_session, bid=bid)
    pub = _publish(owner, bid, pid, conn["connection_id"]).json()["publication"]
    mid = int(pub["remote_message_id"])

    r = owner.patch(f"{API}/businesses/{bid}/products/{pid}", json={"name": "Laptop Pro"})
    assert r.status_code == 200, r.text
    resp = db_client.post(
        f"{API}/businesses/{bid}/publications/{pub['publication_id']}/update"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["plan"] == "EDIT"
    assert body["updated"] is True
    assert body["publication"]["remote_message_id"] == pub["remote_message_id"]
    assert len(fake_bale.remote) == 1

    from app.infrastructure.platforms.bale import escape_markdown

    # Same remote message, updated with the escaped wire form.
    assert fake_bale.remote_text(mid) == escape_markdown(body["publication"]["text"])
    assert "edit_message_text" in fake_bale.call_methods()
    # No inspection call was made (Bale has none).
    assert "get_message" not in fake_bale.call_methods()


def test_update_bale_media_change_reposts_new_then_deletes_old(
    db_client, db_session, fake_bale
):
    owner, _op, bid = _bootstrap(db_client, db_session)
    conn = _connect(owner, bid)
    pid = _seed_product(db_session, bid=bid)
    _add_media(owner, bid, pid, ["https://cdn.example.com/a.jpg"])
    pub = _publish(owner, bid, pid, conn["connection_id"]).json()["publication"]
    old_mid = int(pub["remote_message_id"])
    assert len(fake_bale.remote) == 1

    _add_media(owner, bid, pid, ["https://cdn.example.com/b.jpg"])
    resp = db_client.post(
        f"{API}/businesses/{bid}/publications/{pub['publication_id']}/update"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["plan"] == "REPOST"
    assert body["updated"] is True

    old = body["publication"]
    new = body["new_publication"]
    assert old["status"] == "REMOTE_DELETED"
    assert new["status"] == "PUBLISHED"
    assert new["remote_message_id"] != str(old_mid)

    assert (CANONICAL_CHAT_ID, old_mid) not in fake_bale.remote
    new_mid = int(new["remote_message_id"])
    assert fake_bale.remote[(CANONICAL_CHAT_ID, new_mid)]["media"] == [
        "https://cdn.example.com/a.jpg",
        "https://cdn.example.com/b.jpg",
    ]
    # Order: send new -> (verification by API acceptance) -> delete old.
    # No get_message (Bale has no message lookup).
    methods = fake_bale.call_methods()
    assert methods.index("send_media_group") < methods.index("delete_message")
    assert "get_message" not in methods


def test_bale_48h_old_delete_failure_leaves_new_live_and_old_tracked(
    db_client, db_session, fake_bale
):
    """Owner decision (2026-09-15): Bale only allows deleting messages
    younger than 48h. When the OLD message of a repost is too old, the NEW
    message stays live and the OLD one is tracked (FAILED_FINAL with its
    remote id kept) for manual owner deletion — never silently lost."""
    owner, _op, bid = _bootstrap(db_client, db_session)
    conn = _connect(owner, bid)
    pid = _seed_product(db_session, bid=bid)
    pub = _publish(owner, bid, pid, conn["connection_id"]).json()["publication"]
    old_mid = int(pub["remote_message_id"])

    # Age the remote message past the 48h limit.
    fake_bale.now_hours = 49.0

    resp = db_client.post(
        f"{API}/businesses/{bid}/publications/{pub['publication_id']}/repost"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["updated"] is True
    old = body["publication"]
    new = body["new_publication"]

    # NEW: live with a fresh remote id.
    assert new["status"] == "PUBLISHED"
    new_mid = int(new["remote_message_id"])
    assert new_mid != old_mid
    assert (CANONICAL_CHAT_ID, new_mid) in fake_bale.remote

    # OLD: tracked lingering state — final failure, remote id KEPT so the
    # owner (and the app) can see which message to delete manually.
    assert old["status"] == "FAILED_FINAL"
    assert old["error_code"] == "VALIDATION_ERROR"
    assert old["remote_message_id"] == str(old_mid)
    assert (CANONICAL_CHAT_ID, old_mid) in fake_bale.remote  # still on the remote
    assert len(fake_bale.remote) == 2

    # The slot is held by the NEW live publication.
    again = _publish(owner, bid, pid, conn["connection_id"]).json()
    assert again["created"] is False
    assert again["publication"]["publication_id"] == new["publication_id"]


def test_bale_check_published_is_explictly_unsupported(db_client, db_session, fake_bale):
    owner, _op, bid = _bootstrap(db_client, db_session)
    conn = _connect(owner, bid)
    pid = _seed_product(db_session, bid=bid)
    pub = _publish(owner, bid, pid, conn["connection_id"]).json()["publication"]

    check = db_client.post(
        f"{API}/businesses/{bid}/publications/{pub['publication_id']}/check"
    )
    assert check.status_code == 409, check.text
    msg = check.json()["error"]["message"]
    assert "remote inspection is not supported" in msg
    assert "BALE" in msg


# --- delete / suspension tests -------------------------------------------------


def test_delete_bale_publication_removes_remote(db_client, db_session, fake_bale):
    owner, _op, bid = _bootstrap(db_client, db_session)
    conn = _connect(owner, bid)
    pid = _seed_product(db_session, bid=bid)
    pub = _publish(owner, bid, pid, conn["connection_id"]).json()["publication"]
    mid = int(pub["remote_message_id"])

    resp = db_client.post(
        f"{API}/businesses/{bid}/publications/{pub['publication_id']}/delete"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deleted"] is True
    assert body["publication"]["status"] == "REMOTE_DELETED"
    assert (CANONICAL_CHAT_ID, mid) not in fake_bale.remote


def test_bale_permission_loss_suspends_and_verify_resumes(
    db_client, db_session, fake_bale
):
    owner, _op, bid = _bootstrap(db_client, db_session)
    conn = _connect(owner, bid)
    pid = _seed_product(db_session, bid=bid)
    pub = _publish(owner, bid, pid, conn["connection_id"]).json()["publication"]

    fake_bale.is_admin = False
    v = owner.post(f"{API}/businesses/{bid}/connections/{conn['connection_id']}/verify")
    assert v.status_code == 200, v.text
    assert v.json()["status"] == "PERMISSION_LOST"
    row = db_session.get(Publication, uuid.UUID(pub["publication_id"]))
    assert row.status == "PERMISSION_LOST"

    # Rights restored -> re-verify resumes WITHOUT inspection (Bale has
    # no message lookup) — an explicit resume, not a republish.
    fake_bale.is_admin = True
    v2 = owner.post(f"{API}/businesses/{bid}/connections/{conn['connection_id']}/verify")
    assert v2.status_code == 200, v2.text
    assert v2.json()["status"] == "VERIFIED"
    db_session.expire_all()
    row = db_session.get(Publication, uuid.UUID(pub["publication_id"]))
    assert row.status == "PUBLISHED"
    assert len(fake_bale.remote) == 1  # no new remote message

    # The attempt record documents that no inspection happened.
    from app.infrastructure.db.models import PublicationAttempt

    details = [
        a.error_detail
        for a in db_session.scalars(
            select(PublicationAttempt).where(
                PublicationAttempt.publication_id == uuid.UUID(pub["publication_id"])
            )
        )
        if a.error_detail
    ]
    assert any("no remote inspection" in d for d in details)


def test_bale_disconnect_suspends_and_reconnect_resumes(
    db_client, db_session, fake_bale
):
    owner, _op, bid = _bootstrap(db_client, db_session)
    conn = _connect(owner, bid)
    pid = _seed_product(db_session, bid=bid)
    pub = _publish(owner, bid, pid, conn["connection_id"]).json()["publication"]

    off = owner.post(f"{API}/businesses/{bid}/connections/{conn['connection_id']}/disconnect")
    assert off.status_code == 200
    row = db_session.get(Publication, uuid.UUID(pub["publication_id"]))
    assert row.status == "DISCONNECTED"
    assert len(fake_bale.remote) == 1

    back = owner.post(f"{API}/businesses/{bid}/connections/{conn['connection_id']}/reconnect")
    assert back.status_code == 200
    assert back.json()["status"] == "VERIFIED"
    db_session.expire_all()
    row = db_session.get(Publication, uuid.UUID(pub["publication_id"]))
    assert row.status == "PUBLISHED"
    assert len(fake_bale.remote) == 1


def test_bale_publication_cross_tenant_404(db_client, db_session, fake_bale):
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
