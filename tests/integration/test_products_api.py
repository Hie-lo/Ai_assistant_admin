"""Phase 3 product API — manual edits, media ops, lifecycle, versions."""

from __future__ import annotations

import io
import uuid

import openpyxl
import pytest
from app.infrastructure.db.models import Product
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration

PASSWORD = "correct-horse-battery-1"
_HEADERS = ["Name", "Price", "SKU"]


def _client_for(client: TestClient) -> TestClient:
    return TestClient(client.app)


def _bootstrap(db_client: TestClient, db_session: Session):
    from tests.conftest import make_super_admin

    assert (
        db_client.post(
            "/api/v1/auth/register",
            json={"email": "owner@example.com", "password": PASSWORD, "display_name": "Owner"},
        ).status_code
        == 201
    )
    assert db_client.post(
        "/api/v1/auth/login", json={"email": "owner@example.com", "password": PASSWORD}
    ).status_code == 200
    make_super_admin(db_session, "operator@example.com")
    op = _client_for(db_client)
    assert (
        op.post(
            "/api/v1/auth/login", json={"email": "operator@example.com", "password": PASSWORD}
        ).status_code
        == 200
    )
    bid = db_client.post(
        "/api/v1/businesses", json={"name": "Acme", "business_type_key": "general"}
    ).json()["business_id"]
    sub = db_client.post(
        f"/api/v1/businesses/{bid}/subscriptions", json={"plan_code": "starter"}
    ).json()
    resp = op.post(
        f"/api/v1/subscriptions/{sub['subscription_id']}/payments/verify",
        json={"reference": "IR-TEST-1"},
    )
    assert resp.status_code == 200, resp.text
    return db_client, bid


def _xlsx_bytes(rows: list[list[object]]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _entry(column: str, canonical: str, ftype: str, display: str, required: bool = False) -> dict:
    return {
        "column": column,
        "canonical_field": canonical,
        "field_kind": "CORE",
        "field_type": ftype,
        "display_name": display,
        "required": required,
        "template_exposed": False,
        "confidence": 0.9,
        "evidence": "t",
    }


_ENTRIES = [
    _entry("Name", "name", "STRING", "نام", required=True),
    _entry("Price", "price", "PRICE", "قیمت"),
    _entry("SKU", "sku", "STRING", "کد"),
]


def _seed_one_product(client: TestClient, bid: str) -> Product:
    sid = client.post(
        f"/api/v1/businesses/{bid}/sources", json={"name": "S", "kind": "EXCEL_UPLOAD"}
    ).json()["source_id"]
    resp = client.post(
        f"/api/v1/businesses/{bid}/sources/{sid}/mapping", json={"entries": _ENTRIES}
    )
    mid = resp.json()["mapping_id"]
    client.post(f"/api/v1/businesses/{bid}/sources/{sid}/mappings/{mid}/activate")
    rows = [_HEADERS, ("آب معدنی", 500, "W-1")]
    resp = client.post(
        f"/api/v1/businesses/{bid}/sources/{sid}/imports",
        files={"file": ("f.xlsx", _xlsx_bytes(rows), "application/octet-stream")},
    )
    assert resp.status_code == 202, resp.text
    from app.infrastructure.db.session import get_session_factory

    with get_session_factory()() as s:
        prod = s.scalar(
            select(Product).where(Product.business_id == uuid.UUID(bid), Product.sku == "W-1")
        )
        s.expunge(prod)
        return prod


def test_get_and_list_products(
    db_client: TestClient, db_session: Session
) -> None:
    owner, bid = _bootstrap(db_client, db_session)
    prod = _seed_one_product(owner, bid)

    got = owner.get(f"/api/v1/businesses/{bid}/products/{prod.product_id}")
    assert got.status_code == 200
    body = got.json()
    assert body["name"] == "آب معدنی"
    assert body["price"] == 500
    assert body["lifecycle_state"] == "ACTIVE"
    assert body["currency"] == "IRT"

    listing = owner.get(f"/api/v1/businesses/{bid}/products").json()
    assert len(listing) == 1

    # Query filter.
    found = owner.get(f"/api/v1/businesses/{bid}/products", params={"q": "آب معدنی"}).json()
    assert len(found) == 1
    none = owner.get(f"/api/v1/businesses/{bid}/products", params={"q": "nope"}).json()
    assert none == []

    # State filter.
    missing = owner.get(
        f"/api/v1/businesses/{bid}/products", params={"state": "MISSING_FROM_SOURCE"}
    ).json()
    assert missing == []


def test_manual_price_edit_bumps_version(
    db_client: TestClient, db_session: Session
) -> None:
    owner, bid = _bootstrap(db_client, db_session)
    prod = _seed_one_product(owner, bid)

    resp = owner.patch(
        f"/api/v1/businesses/{bid}/products/{prod.product_id}", json={"price": 550}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["price"] == 550
    assert resp.json()["current_version"] == 2

    versions = owner.get(f"/api/v1/businesses/{bid}/products/{prod.product_id}/versions").json()
    assert len(versions) == 2
    assert versions[0]["version_no"] == 2
    assert versions[0]["change_categories"] == ["PRICE_CHANGED"]
    assert versions[0]["changed_fields"]["price"] == {"old": 500, "new": 550}
    assert versions[0]["trigger"] == "MANUAL"


def test_noop_edit_creates_no_version(
    db_client: TestClient, db_session: Session
) -> None:
    owner, bid = _bootstrap(db_client, db_session)
    prod = _seed_one_product(owner, bid)
    resp = owner.patch(
        f"/api/v1/businesses/{bid}/products/{prod.product_id}", json={"price": 500}
    )
    assert resp.status_code == 200
    assert resp.json()["current_version"] == 1


def test_manual_sku_edit_is_critical_and_review_required(
    db_client: TestClient, db_session: Session
) -> None:
    owner, bid = _bootstrap(db_client, db_session)
    prod = _seed_one_product(owner, bid)
    resp = owner.patch(
        f"/api/v1/businesses/{bid}/products/{prod.product_id}", json={"sku": "W-9"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["sku"] == "W-9"
    assert body["lifecycle_state"] == "REVIEW_REQUIRED"
    versions = owner.get(f"/api/v1/businesses/{bid}/products/{prod.product_id}/versions").json()
    assert versions[0]["risk_level"] == "CRITICAL"
    assert versions[0]["change_categories"] == ["IDENTITY_IDENTIFIER_CHANGED"]
    assert versions[0]["changed_fields"]["sku"]["retained_old"] == "W-1"


def test_custom_attributes_edit(
    db_client: TestClient, db_session: Session
) -> None:
    owner, bid = _bootstrap(db_client, db_session)
    prod = _seed_one_product(owner, bid)
    resp = owner.patch(
        f"/api/v1/businesses/{bid}/products/{prod.product_id}",
        json={"attributes": {"وزن": "330 گرم", "منشأ": "ایران"}},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["attributes"]["وزن"] == "330 گرم"
    versions = owner.get(f"/api/v1/businesses/{bid}/products/{prod.product_id}/versions").json()
    assert versions[0]["change_categories"] == ["CUSTOM_FIELD_CHANGED"]
    assert versions[0]["risk_level"] == "HIGH"


def test_media_add_list_remove(
    db_client: TestClient, db_session: Session
) -> None:
    owner, bid = _bootstrap(db_client, db_session)
    prod = _seed_one_product(owner, bid)
    pid = str(prod.product_id)

    r1 = owner.post(
        f"/api/v1/businesses/{bid}/products/{pid}/media",
        json={"url": "https://cdn.example.com/c1.jpg", "origin": "CUSTOMER"},
    )
    assert r1.status_code == 201, r1.text
    media_id = r1.json()["media_id"]

    # Invalid URL rejected.
    bad = owner.post(
        f"/api/v1/businesses/{bid}/products/{pid}/media",
        json={"url": "ftp://nope", "origin": "CUSTOMER"},
    )
    assert bad.status_code == 422

    media = owner.get(f"/api/v1/businesses/{bid}/products/{pid}/media").json()
    assert len(media) == 1
    assert media[0]["origin"] == "CUSTOMER"
    assert media[0]["position"] == 0

    # Remove (soft) then list again.
    resp = owner.delete(f"/api/v1/businesses/{bid}/products/{pid}/media/{media_id}")
    assert resp.status_code == 204
    media = owner.get(f"/api/v1/businesses/{bid}/products/{pid}/media").json()
    assert len(media) == 1
    assert media[0]["status"] == "REMOVED"


def test_archive_and_restore(
    db_client: TestClient, db_session: Session
) -> None:
    owner, bid = _bootstrap(db_client, db_session)
    prod = _seed_one_product(owner, bid)
    pid = str(prod.product_id)

    resp = owner.post(f"/api/v1/businesses/{bid}/products/{pid}/archive")
    assert resp.status_code == 200, resp.text
    assert resp.json()["lifecycle_state"] == "ARCHIVED"

    # Archive again -> 409.
    resp = owner.post(f"/api/v1/businesses/{bid}/products/{pid}/archive")
    assert resp.status_code == 409

    # Restore -> ACTIVE again; restoring a non-archived product then fails.
    other_bid_check = owner.post(f"/api/v1/businesses/{bid}/products/{pid}/restore")
    assert other_bid_check.status_code == 200
    assert other_bid_check.json()["lifecycle_state"] == "ACTIVE"
    resp = owner.post(f"/api/v1/businesses/{bid}/products/{pid}/restore")
    assert resp.status_code == 409

    # Archived products are excluded from the default list.
    owner.post(f"/api/v1/businesses/{bid}/products/{pid}/archive")
    listing = owner.get(f"/api/v1/businesses/{bid}/products").json()
    assert [p["product_id"] for p in listing] != [pid]
    archived = owner.get(f"/api/v1/businesses/{bid}/products", params={"state": "ARCHIVED"}).json()
    assert [p["product_id"] for p in archived] == [pid]


def test_cross_tenant_404s(
    db_client: TestClient, db_session: Session
) -> None:
    owner, bid = _bootstrap(db_client, db_session)
    prod = _seed_one_product(owner, bid)
    pid = str(prod.product_id)

    other = _client_for(db_client)
    assert (
        other.post(
            "/api/v1/auth/register",
            json={"email": "other@example.com", "password": PASSWORD, "display_name": "Other"},
        ).status_code
        == 201
    )
    other.post("/api/v1/auth/login", json={"email": "other@example.com", "password": PASSWORD})
    other_bid = other.post(
        "/api/v1/businesses", json={"name": "Other", "business_type_key": "general"}
    ).json()["business_id"]

    assert other.get(f"/api/v1/businesses/{other_bid}/products/{pid}").status_code == 404
    assert (
        other.patch(f"/api/v1/businesses/{other_bid}/products/{pid}", json={"price": 1}).status_code
        == 404
    )
    # Media endpoints also 404 cross-tenant (product resolution first).
    assert (
        other.get(f"/api/v1/businesses/{other_bid}/products/{pid}/media").status_code == 404
    )
