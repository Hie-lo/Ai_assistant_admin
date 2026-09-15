"""Phase 3 import pipeline — full-stack integration tests (DB-backed).

Covers the owner-approved safety matrix:
- happy path + idempotent re-sync (row fingerprint)
- missing-row inference (non-destructive) + reappearance reconnect
- row move keeps identity (locator is not identity)
- ambiguous / conflict / duplicate identity -> review case, NEVER auto-merge
- identity-field change -> CRITICAL + REVIEW_REQUIRED + frozen
- media: SOURCE vs CUSTOMER protection (default + media_authoritative)
- entitlement gates (product limit, source limit, daily sync frequency)
- corrupt file -> FAILED run, no missing inference
- preview = zero writes
- cross-tenant 404 + permission 403 + audit trail
"""

from __future__ import annotations

import io
import uuid

import openpyxl
import pytest
from app.infrastructure.db.models import AuditLog, ImportRun, Product
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration

PASSWORD = "correct-horse-battery-1"
_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# --- helpers ---------------------------------------------------------------------


def _client_for(client: TestClient) -> TestClient:
    return TestClient(client.app)


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


def _bootstrap(db_client: TestClient, db_session: Session):
    """owner client + operator client + business with ACTIVE starter subscription."""
    from tests.conftest import make_super_admin

    _register_login(db_client, "owner@example.com", "Owner")
    make_super_admin(db_session, "operator@example.com")
    operator = _client_for(db_client)
    assert (
        operator.post(
            "/api/v1/auth/login", json={"email": "operator@example.com", "password": PASSWORD}
        ).status_code
        == 200
    )
    biz = db_client.post(
        "/api/v1/businesses", json={"name": "Acme", "business_type_key": "general"}
    ).json()
    bid = biz["business_id"]
    sub = db_client.post(
        f"/api/v1/businesses/{bid}/subscriptions", json={"plan_code": "starter"}
    ).json()
    resp = operator.post(
        f"/api/v1/subscriptions/{sub['subscription_id']}/payments/verify",
        json={"reference": "IR-TEST-1"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["subscription"]["status"] == "ACTIVE"
    return db_client, operator, bid


def _xlsx_bytes(rows: list[list[object]]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


_HEADERS = ["Name", "Price", "SKU", "Category", "Image"]


def _mapping_entries() -> list[dict]:
    return [
        {"column": "Name", "canonical_field": "name", "field_kind": "CORE",
         "field_type": "STRING", "display_name": "نام", "required": True,
         "template_exposed": False, "confidence": 0.9, "evidence": "test"},
        {"column": "Price", "canonical_field": "price", "field_kind": "CORE",
         "field_type": "PRICE", "display_name": "قیمت", "required": False,
         "template_exposed": False, "confidence": 0.9, "evidence": "test"},
        {"column": "SKU", "canonical_field": "sku", "field_kind": "CORE",
         "field_type": "STRING", "display_name": "کد", "required": False,
         "template_exposed": False, "confidence": 0.9, "evidence": "test"},
        {"column": "Category", "canonical_field": "category", "field_kind": "CORE",
         "field_type": "STRING", "display_name": "دسته", "required": False,
         "template_exposed": False, "confidence": 0.9, "evidence": "test"},
        {"column": "Image", "canonical_field": "image1", "field_kind": "CORE",
         "field_type": "MEDIA_URL", "display_name": "تصویر", "required": False,
         "template_exposed": False, "confidence": 0.9, "evidence": "test"},
    ]


def _create_source(client: TestClient, bid: str, name: str = "Main Excel", **extra) -> str:
    body = {"name": name, "kind": "EXCEL_UPLOAD"}
    body.update(extra)
    resp = client.post(f"/api/v1/businesses/{bid}/sources", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()["source_id"]


def _propose_and_activate(client: TestClient, bid: str, sid: str) -> str:
    resp = client.post(
        f"/api/v1/businesses/{bid}/sources/{sid}/mapping", json={"entries": _mapping_entries()}
    )
    assert resp.status_code == 201, resp.text
    mid = resp.json()["mapping_id"]
    assert resp.json()["status"] == "DRAFT"
    resp = client.post(f"/api/v1/businesses/{bid}/sources/{sid}/mappings/{mid}/activate")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "ACTIVE"
    return mid


def _rows(*rows: tuple) -> list[list[object]]:
    return [_HEADERS, *list(rows)]


def _import(client: TestClient, bid: str, sid: str, rows: list[list[object]]):
    return client.post(
        f"/api/v1/businesses/{bid}/sources/{sid}/imports",
        files={"file": ("import.xlsx", _xlsx_bytes(rows), _XLSX_MIME)},
    )


def _preview(client: TestClient, bid: str, sid: str, rows: list[list[object]]):
    return client.post(
        f"/api/v1/businesses/{bid}/sources/{sid}/imports/preview",
        files={"file": ("import.xlsx", _xlsx_bytes(rows), _XLSX_MIME)},
    )


def _products(db_session: Session, bid: str) -> list[Product]:
    return list(
        db_session.scalars(select(Product).where(Product.business_id == uuid.UUID(bid))).all()
    )


def _product_by_sku(db_session: Session, bid: str, sku: str) -> Product | None:
    return db_session.scalar(
        select(Product).where(
            Product.business_id == uuid.UUID(bid), Product.sku == sku
        )
    )


def _runs(db_session: Session, bid: str) -> list[ImportRun]:
    return list(
        db_session.scalars(select(ImportRun).where(ImportRun.business_id == uuid.UUID(bid))).all()
    )


def _suggest(client: TestClient, bid: str, sid: str, rows: list[list[object]]):
    return client.post(
        f"/api/v1/businesses/{bid}/sources/{sid}/mapping/suggest",
        files={"file": ("import.xlsx", _xlsx_bytes(rows), _XLSX_MIME)},
    )


# --- happy path --------------------------------------------------------------------


def test_import_creates_products_and_reimport_is_idempotent(
    db_client: TestClient, db_session: Session
) -> None:
    owner, _op, bid = _bootstrap(db_client, db_session)
    sid = _create_source(owner, bid)
    _propose_and_activate(owner, bid, sid)

    rows = _rows(
        ("آب معدنی", 500, "W-1", "نوشیدنی", "https://cdn.example.com/a.jpg"),
        ("شیر", 1200, "M-1", "لبنی", "https://cdn.example.com/m.jpg"),
        ("نان", 300, "B-1", "نونی", ""),
    )
    resp = _import(owner, bid, sid, rows)
    assert resp.status_code == 202, resp.text
    run = resp.json()
    assert run["status"] == "SUCCEEDED"
    counts = run["counts"]
    assert counts["read"] == 3
    assert counts["valid"] == 3
    assert counts["new"] == 3
    assert counts["unchanged"] == 0

    prods = _products(db_session, bid)
    assert len(prods) == 3
    water = _product_by_sku(db_session, bid, "W-1")
    assert water is not None
    assert water.name == "آب معدنی"
    assert water.price == 500
    assert water.currency == "IRT"
    assert water.category == "نوشیدنی"
    assert water.lifecycle_state == "ACTIVE"
    assert water.current_version == 1
    assert water.fingerprint

    # Idempotent re-sync: identical content -> no new versions.
    resp2 = _import(owner, bid, sid, rows)
    assert resp2.status_code == 202, resp2.text
    counts2 = resp2.json()["counts"]
    assert counts2["unchanged"] == 3
    assert counts2["new"] == 0
    assert counts2["changed"] == 0
    assert len(_products(db_session, bid)) == 3
    assert water.current_version == 1


def test_mapping_versioning_supersedes_old_active(
    db_client: TestClient, db_session: Session
) -> None:
    owner, _op, bid = _bootstrap(db_client, db_session)
    sid = _create_source(owner, bid)
    mid1 = _propose_and_activate(owner, bid, sid)

    # A second version can be proposed and activated; the first is superseded.
    resp = owner.post(
        f"/api/v1/businesses/{bid}/sources/{sid}/mapping", json={"entries": _mapping_entries()}
    )
    assert resp.status_code == 201, resp.text
    mid2 = resp.json()["mapping_id"]
    assert resp.json()["version"] == 2
    resp = owner.post(f"/api/v1/businesses/{bid}/sources/{sid}/mappings/{mid2}/activate")
    assert resp.status_code == 200, resp.text

    versions = owner.get(f"/api/v1/businesses/{bid}/sources/{sid}/mappings").json()
    by_mid = {m["mapping_id"]: m for m in versions}
    assert by_mid[mid1]["status"] == "SUPERSEDED"
    assert by_mid[mid2]["status"] == "ACTIVE"

    # A superseded mapping cannot be re-activated.
    resp = owner.post(f"/api/v1/businesses/{bid}/sources/{sid}/mappings/{mid1}/activate")
    assert resp.status_code == 409


def test_mapping_proposal_requires_name_column(
    db_client: TestClient, db_session: Session
) -> None:
    owner, _op, bid = _bootstrap(db_client, db_session)
    sid = _create_source(owner, bid)
    resp = owner.post(
        f"/api/v1/businesses/{bid}/sources/{sid}/mapping",
        json={"entries": [e for e in _mapping_entries() if e["canonical_field"] != "name"]},
    )
    assert resp.status_code == 422


def test_suggest_mapping_persian_headers(
    db_client: TestClient, db_session: Session
) -> None:
    owner, _op, bid = _bootstrap(db_client, db_session)
    sid = _create_source(owner, bid)
    persian_headers = ["نام", "قیمت", "توضیحات", "موجودی", "دسته", "کد کالا", "بارکد", "تصویر"]
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(persian_headers)
    ws.append(["آب معدنی", 500, "توضیح", 10, "نوشیدنی", "W-1", "6281", "https://x/y.jpg"])
    buf = io.BytesIO()
    wb.save(buf)
    resp = owner.post(
        f"/api/v1/businesses/{bid}/sources/{sid}/mapping/suggest",
        files={"file": ("f.xlsx", buf.getvalue(), _XLSX_MIME)},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["headers"] == persian_headers
    by_col = {s["column"]: s for s in body["suggestions"]}
    assert by_col["نام"]["canonical_field"] == "name"
    assert by_col["قیمت"]["canonical_field"] == "price"
    assert by_col["کد کالا"]["canonical_field"] == "sku"
    assert by_col["تصویر"]["canonical_field"] == "image1"


# --- change detection ----------------------------------------------------------------


def test_price_change_versions_and_risk_classification(
    db_client: TestClient, db_session: Session
) -> None:
    owner, _op, bid = _bootstrap(db_client, db_session)
    sid = _create_source(owner, bid)
    _propose_and_activate(owner, bid, sid)
    v1 = _rows(("آب معدنی", 1000, "W-1", "نوشیدنی", ""))
    assert _import(owner, bid, sid, v1).status_code == 202

    # +5% -> MEDIUM risk, PRICE_CHANGED category.
    v2 = _rows(("آب معدنی", 1050, "W-1", "نوشیدنی", ""))
    resp = _import(owner, bid, sid, v2)
    assert resp.status_code == 202, resp.text
    assert resp.json()["counts"]["changed"] == 1

    # +90% jump -> HIGH risk.
    v3 = _rows(("آب معدنی", 2000, "W-1", "نوشیدنی", ""))
    resp = _import(owner, bid, sid, v3)
    assert resp.status_code == 202, resp.text
    prod = _product_by_sku(db_session, bid, "W-1")
    assert prod.current_version == 3
    versions = owner.get(f"/api/v1/businesses/{bid}/products/{prod.product_id}/versions").json()
    assert versions[0]["version_no"] == 3
    assert versions[0]["risk_level"] == "HIGH"
    assert versions[0]["change_categories"] == ["PRICE_CHANGED"]
    assert versions[1]["version_no"] == 2
    assert versions[1]["risk_level"] == "MEDIUM"


def test_name_change_is_low_risk_category(
    db_client: TestClient, db_session: Session
) -> None:
    owner, _op, bid = _bootstrap(db_client, db_session)
    sid = _create_source(owner, bid)
    _propose_and_activate(owner, bid, sid)
    assert _import(owner, bid, sid, _rows(("آب معدنی", 1000, "W-1", "", ""))).status_code == 202
    resp = _import(owner, bid, sid, _rows(("آب معدنی قارچ", 1000, "W-1", "", "")))
    assert resp.status_code == 202, resp.text
    prod = _product_by_sku(db_session, bid, "W-1")
    versions = owner.get(f"/api/v1/businesses/{bid}/products/{prod.product_id}/versions").json()
    assert versions[0]["risk_level"] == "LOW"
    assert versions[0]["change_categories"] == ["NAME_CHANGED"]


# --- missing / reappearance / moves ---------------------------------------------------


def test_missing_inference_and_reappearance(
    db_client: TestClient, db_session: Session
) -> None:
    owner, _op, bid = _bootstrap(db_client, db_session)
    sid = _create_source(owner, bid)
    _propose_and_activate(owner, bid, sid)
    full = _rows(
        ("آب معدنی", 500, "W-1", "", ""),
        ("شیر", 1200, "M-1", "", ""),
        ("نان", 300, "B-1", "", ""),
    )
    assert _import(owner, bid, sid, full).status_code == 202

    # Row M-1 disappears from the source.
    without_milk = _rows(("آب معدنی", 500, "W-1", "", ""), ("نان", 300, "B-1", "", ""))
    resp = _import(owner, bid, sid, without_milk)
    assert resp.status_code == 202, resp.text
    assert resp.json()["counts"]["missing"] == 1
    milk = _product_by_sku(db_session, bid, "M-1")
    assert milk.lifecycle_state == "MISSING_FROM_SOURCE"

    # Reappearance reconnects automatically (same SKU, moved row).
    with_milk_back = _rows(
        ("آب معدنی", 500, "W-1", "", ""),
        ("شیر", 1200, "M-1", "", ""),
        ("نان", 300, "B-1", "", ""),
    )
    resp = _import(owner, bid, sid, with_milk_back)
    assert resp.status_code == 202, resp.text
    assert resp.json()["counts"]["reappeared"] == 1
    db_session.expire_all()  # drop identity-map cache from the pre-reappearance read
    milk = _product_by_sku(db_session, bid, "M-1")
    assert milk.lifecycle_state == "ACTIVE"


def test_row_move_keeps_identity(
    db_client: TestClient, db_session: Session
) -> None:
    owner, _op, bid = _bootstrap(db_client, db_session)
    sid = _create_source(owner, bid)
    _propose_and_activate(owner, bid, sid)
    v1 = _rows(("آب معدنی", 500, "W-1", "", ""), ("شیر", 1200, "M-1", "", ""))
    assert _import(owner, bid, sid, v1).status_code == 202

    # The two rows swap positions; content identical -> identity kept, no missing.
    v2 = _rows(("شیر", 1200, "M-1", "", ""), ("آب معدنی", 500, "W-1", "", ""))
    resp = _import(owner, bid, sid, v2)
    assert resp.status_code == 202, resp.text
    counts = resp.json()["counts"]
    assert counts["missing"] == 0
    assert counts["unchanged"] == 2
    assert len(_products(db_session, bid)) == 2


def test_mass_missing_over_half_blocks_inference(
    db_client: TestClient, db_session: Session
) -> None:
    owner, _op, bid = _bootstrap(db_client, db_session)
    sid = _create_source(owner, bid)
    _propose_and_activate(owner, bid, sid)
    rows = _rows(*[(f"Product {i}", 100 + i, f"P-{i}", "", "") for i in range(10)])
    assert _import(owner, bid, sid, rows).status_code == 202

    # Only 2 of 10 remain: 8 unseen > 50% of baseline -> blocked.
    few = _rows(("Product 0", 100, "P-0", "", ""), ("Product 1", 101, "P-1", "", ""))
    resp = _import(owner, bid, sid, few)
    assert resp.status_code == 202, resp.text
    assert resp.json()["counts"]["missing"] == 0
    prod = _product_by_sku(db_session, bid, "P-5")
    assert prod.lifecycle_state == "ACTIVE"  # NOT marked missing
    cases = owner.get(f"/api/v1/businesses/{bid}/review-cases").json()
    assert any(c["kind"] == "MASS_MISSING_BLOCKED" for c in cases)


# --- identity edge cases ---------------------------------------------------------------


def test_ambiguous_sku_creates_review_case_and_no_merge(
    db_client: TestClient, db_session: Session
) -> None:
    owner, _op, bid = _bootstrap(db_client, db_session)
    sid = _create_source(owner, bid)
    _propose_and_activate(owner, bid, sid)
    assert _import(owner, bid, sid, _rows(("آب معدنی", 500, "DUP-1", "", ""))).status_code == 202

    # A second product with the same SKU exists (data error in the business).
    db_session.add(
        Product(
            business_id=uuid.UUID(bid),
            name="آب گازدار",
            sku="DUP-1",
            current_version=1,
            attributes={},
        )
    )
    db_session.commit()

    resp = _import(owner, bid, sid, _rows(("آب معدنی", 500, "DUP-1", "", "")))
    assert resp.status_code == 202, resp.text
    counts = resp.json()["counts"]
    assert counts["ambiguous"] == 1
    assert counts["new"] == 0  # NOT auto-created
    assert len(_products(db_session, bid)) == 2  # NOT auto-merged

    cases = owner.get(f"/api/v1/businesses/{bid}/review-cases").json()
    case = next(c for c in cases if c["kind"] == "IDENTITY_AMBIGUOUS")
    assert len(case["payload"]["candidates"]) == 2

    # Resolve: merge into the first product.
    target = case["payload"]["candidates"][0]["product_id"]
    resp = owner.post(
        f"/api/v1/businesses/{bid}/review-cases/{case['case_id']}/resolve",
        json={"action": "MERGE_TO", "target_product_id": target},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "RESOLVED"
    assert resp.json()["resolution"] == f"merged_into:{target}"
    # Re-resolving is rejected.
    again = owner.post(
        f"/api/v1/businesses/{bid}/review-cases/{case['case_id']}/resolve",
        json={"action": "MERGE_TO", "target_product_id": target},
    )
    assert again.status_code == 409


def test_duplicate_candidate_on_name_only_match(
    db_client: TestClient, db_session: Session
) -> None:
    owner, _op, bid = _bootstrap(db_client, db_session)
    sid = _create_source(owner, bid)
    _propose_and_activate(owner, bid, sid)
    # No SKU in the mapping: strip it.
    owner2 = owner
    entries = [e for e in _mapping_entries() if e["canonical_field"] != "sku"]
    resp = owner2.post(
        f"/api/v1/businesses/{bid}/sources/{sid}/mapping", json={"entries": entries}
    )
    assert resp.status_code == 201, resp.text
    mid = resp.json()["mapping_id"]
    resp = owner2.post(f"/api/v1/businesses/{bid}/sources/{sid}/mappings/{mid}/activate")
    assert resp.status_code == 200, resp.text

    assert _import(owner, bid, sid, _rows(("آب معدنی", 500, "", "نوشیدنی", ""))).status_code == 202

    # Same name, different category -> different fingerprint, name-only collision.
    resp = _import(owner, bid, sid, _rows(("آب معدنی", 600, "", "لبنی", "")))
    assert resp.status_code == 202, resp.text
    assert resp.json()["counts"]["duplicate"] == 1
    assert len(_products(db_session, bid)) == 1  # no auto-merge, no auto-new

    cases = owner.get(f"/api/v1/businesses/{bid}/review-cases").json()
    case = next(c for c in cases if c["kind"] == "DUPLICATE_CANDIDATE")
    resp = owner.post(
        f"/api/v1/businesses/{bid}/review-cases/{case['case_id']}/resolve",
        json={"action": "CREATE_NEW"},
    )
    assert resp.status_code == 200, resp.text
    assert len(_products(db_session, bid)) == 2


def test_identity_conflict_external_id_vs_sku(
    db_client: TestClient, db_session: Session
) -> None:
    owner, _op, bid = _bootstrap(db_client, db_session)
    sid = _create_source(owner, bid)
    entries = _mapping_entries() + [
        {
            "column": "ExtID", "canonical_field": "external_id", "field_kind": "CORE",
            "field_type": "STRING", "display_name": "شناسه", "required": False,
            "template_exposed": False, "confidence": 0.9, "evidence": "test",
        }
    ]
    resp = owner.post(f"/api/v1/businesses/{bid}/sources/{sid}/mapping", json={"entries": entries})
    assert resp.status_code == 201, resp.text
    mid = resp.json()["mapping_id"]
    owner.post(f"/api/v1/businesses/{bid}/sources/{sid}/mappings/{mid}/activate")

    headers = _HEADERS + ["ExtID"]
    assert _import(
        owner, bid, sid, [headers, ("آب معدنی", 500, "W-1", "", "", "E-1")]
    ).status_code == 202
    # Another product holds SKU X-2.
    db_session.add(
        Product(
            business_id=uuid.UUID(bid), name="محصول دیگر", sku="X-2",
            current_version=1, attributes={},
        )
    )
    db_session.commit()

    # Row claims E-1 (product A) but SKU X-2 (product B) -> conflict.
    resp = _import(owner, bid, sid, [headers, ("آب معدنی", 500, "X-2", "", "", "E-1")])
    assert resp.status_code == 202, resp.text
    assert resp.json()["counts"]["conflict"] == 1

    cases = owner.get(f"/api/v1/businesses/{bid}/review-cases").json()
    case = next(c for c in cases if c["kind"] == "IDENTITY_CONFLICT")
    # KEEP_EXISTING: apply the row to the SKU-matched product WITHOUT the ext id.
    resp = owner.post(
        f"/api/v1/businesses/{bid}/review-cases/{case['case_id']}/resolve",
        json={"action": "KEEP_EXISTING"},
    )
    assert resp.status_code == 200, resp.text
    other = _product_by_sku(db_session, bid, "X-2")
    assert other.external_id is None  # conflicting ID was NOT applied
    water = _product_by_sku(db_session, bid, "W-1")
    assert water.external_id == "E-1"  # original owner keeps it


def test_sku_change_is_critical_review_required_and_freezes_sync(
    db_client: TestClient, db_session: Session
) -> None:
    owner, _op, bid = _bootstrap(db_client, db_session)
    sid = _create_source(owner, bid)
    _propose_and_activate(owner, bid, sid)
    resp = _import(owner, bid, sid, _rows(("آب معدنی", 500, "W-1", "نوشیدنی", "")))
    assert resp.status_code == 202

    # SKU changes W-1 -> W-2 (same product by fingerprint: name+category).
    resp = _import(owner, bid, sid, _rows(("آب معدنی", 500, "W-2", "نوشیدنی", "")))
    assert resp.status_code == 202, resp.text
    prod = _product_by_sku(db_session, bid, "W-2")
    assert prod is not None
    assert prod.lifecycle_state == "REVIEW_REQUIRED"
    versions = owner.get(f"/api/v1/businesses/{bid}/products/{prod.product_id}/versions").json()
    assert versions[0]["risk_level"] == "CRITICAL"
    assert "IDENTITY_IDENTIFIER_CHANGED" in versions[0]["change_categories"]
    assert versions[0]["changed_fields"]["sku"]["retained_old"] == "W-1"

    # While frozen, automatic updates are blocked.
    resp = _import(owner, bid, sid, _rows(("آب معدنی", 510, "W-2", "نوشیدنی", "")))
    assert resp.status_code == 202, resp.text
    assert resp.json()["counts"]["blocked"] == 1
    assert _product_by_sku(db_session, bid, "W-2").price == 500  # unchanged

    # Owner resolves the review case path: manual edit back keeps it reviewable;
    # archive is also available. Restore ACTIVE via manual edit of a non-identity
    # field is NOT enough — lifecycle stays REVIEW_REQUIRED until owner action.
    # (Owner archives to settle.)
    resp = owner.post(f"/api/v1/businesses/{bid}/products/{prod.product_id}/archive")
    assert resp.status_code == 200, resp.text


def test_external_id_held_by_two_products_is_conflict(
    db_client: TestClient, db_session: Session
) -> None:
    owner, _op, bid = _bootstrap(db_client, db_session)
    sid = _create_source(owner, bid)
    entries = _mapping_entries() + [
        {
            "column": "ExtID", "canonical_field": "external_id", "field_kind": "CORE",
            "field_type": "STRING", "display_name": "شناسه", "required": False,
            "template_exposed": False, "confidence": 0.9, "evidence": "test",
        }
    ]
    resp = owner.post(f"/api/v1/businesses/{bid}/sources/{sid}/mapping", json={"entries": entries})
    mid = resp.json()["mapping_id"]
    owner.post(f"/api/v1/businesses/{bid}/sources/{sid}/mappings/{mid}/activate")
    headers = _HEADERS + ["ExtID"]
    assert _import(owner, bid, sid, [headers, ("آب", 500, "W-1", "", "", "E-9")]).status_code == 202
    db_session.add(
        Product(
            business_id=uuid.UUID(bid), name="دیگر", sku="Z-9", external_id="E-9",
            current_version=1, attributes={},
        )
    )
    db_session.commit()
    resp = _import(owner, bid, sid, [headers, ("آب", 500, "W-1", "", "", "E-9")])
    assert resp.status_code == 202, resp.text
    assert resp.json()["counts"]["conflict"] == 1
    cases = owner.get(f"/api/v1/businesses/{bid}/review-cases").json()
    assert any(c["kind"] == "IDENTITY_CONFLICT" for c in cases)


# --- media -----------------------------------------------------------------------------


def test_customer_media_protected_from_source_refresh(
    db_client: TestClient, db_session: Session
) -> None:
    owner, _op, bid = _bootstrap(db_client, db_session)
    sid = _create_source(owner, bid)
    _propose_and_activate(owner, bid, sid)
    assert _import(
        owner, bid, sid, _rows(("آب معدنی", 500, "W-1", "", "https://cdn.example.com/1.jpg"))
    ).status_code == 202
    prod = _product_by_sku(db_session, bid, "W-1")

    # Owner adds a customer photo.
    resp = owner.post(
        f"/api/v1/businesses/{bid}/products/{prod.product_id}/media",
        json={"url": "https://cdn.example.com/customer.jpg", "origin": "CUSTOMER"},
    )
    assert resp.status_code == 201, resp.text

    # Source refresh brings a NEW source URL (old source URL dropped).
    assert _import(
        owner, bid, sid, _rows(("آب معدنی", 500, "W-1", "", "https://cdn.example.com/2.jpg"))
    ).status_code == 202
    media = owner.get(f"/api/v1/businesses/{bid}/products/{prod.product_id}/media").json()
    urls = {(m["url"], m["origin"], m["status"]) for m in media}
    assert ("https://cdn.example.com/1.jpg", "SOURCE", "ACTIVE") in urls  # kept (not authoritative)
    assert ("https://cdn.example.com/2.jpg", "SOURCE", "ACTIVE") in urls  # added
    assert ("https://cdn.example.com/customer.jpg", "CUSTOMER", "ACTIVE") in urls  # protected


def test_media_authoritative_soft_removes_stale_source_media(
    db_client: TestClient, db_session: Session
) -> None:
    owner, _op, bid = _bootstrap(db_client, db_session)
    sid = _create_source(owner, bid, media_authoritative=True)
    _propose_and_activate(owner, bid, sid)
    assert _import(
        owner, bid, sid, _rows(("آب معدنی", 500, "W-1", "", "https://cdn.example.com/1.jpg"))
    ).status_code == 202
    prod = _product_by_sku(db_session, bid, "W-1")
    owner.post(
        f"/api/v1/businesses/{bid}/products/{prod.product_id}/media",
        json={"url": "https://cdn.example.com/customer.jpg", "origin": "CUSTOMER"},
    )

    # New source read without the old URL -> old SOURCE media soft-removed,
    # customer media untouched.
    assert _import(
        owner, bid, sid, _rows(("آب معدنی", 500, "W-1", "", "https://cdn.example.com/2.jpg"))
    ).status_code == 202
    media = owner.get(f"/api/v1/businesses/{bid}/products/{prod.product_id}/media").json()
    by_url = {m["url"]: m for m in media}
    assert by_url["https://cdn.example.com/1.jpg"]["status"] == "REMOVED"
    assert by_url["https://cdn.example.com/2.jpg"]["status"] == "ACTIVE"
    assert by_url["https://cdn.example.com/customer.jpg"]["status"] == "ACTIVE"


def test_invalid_media_url_row_is_invalid_type(
    db_client: TestClient, db_session: Session
) -> None:
    owner, _op, bid = _bootstrap(db_client, db_session)
    sid = _create_source(owner, bid)
    _propose_and_activate(owner, bid, sid)
    resp = _import(owner, bid, sid, _rows(("آب معدنی", 500, "W-1", "", "not-a-url")))
    assert resp.status_code == 202, resp.text
    run = resp.json()
    assert run["counts"]["invalid"] == 1
    assert run["status"] == "SUCCEEDED_WITH_ERRORS"
    assert len(_products(db_session, bid)) == 0


def test_missing_name_row_is_invalid_required(
    db_client: TestClient, db_session: Session
) -> None:
    owner, _op, bid = _bootstrap(db_client, db_session)
    sid = _create_source(owner, bid)
    _propose_and_activate(owner, bid, sid)
    resp = _import(owner, bid, sid, _rows(("", 500, "W-1", "", "")))
    assert resp.status_code == 202, resp.text
    assert resp.json()["counts"]["invalid"] == 1
    row_errors = resp.json()["row_errors"]
    assert row_errors[0]["errors"][0].startswith("name is required")


# --- read failure / preview ---------------------------------------------------------------


def test_corrupt_file_fails_run_without_missing_inference(
    db_client: TestClient, db_session: Session
) -> None:
    owner, _op, bid = _bootstrap(db_client, db_session)
    sid = _create_source(owner, bid)
    _propose_and_activate(owner, bid, sid)
    assert _import(
        owner, bid, sid,
        _rows(("آب معدنی", 500, "W-1", "", ""), ("شیر", 1200, "M-1", "", "")),
    ).status_code == 202

    # Corrupt upload -> FAILED run, no state changes.
    resp = owner.post(
        f"/api/v1/businesses/{bid}/sources/{sid}/imports",
        files={"file": ("bad.xlsx", b"garbage-bytes", "application/octet-stream")},
    )
    assert resp.status_code == 202, resp.text
    run = resp.json()
    assert run["status"] == "FAILED"
    assert run["failure_summary"]
    water = _product_by_sku(db_session, bid, "W-1")
    milk = _product_by_sku(db_session, bid, "M-1")
    assert water.lifecycle_state == "ACTIVE"
    assert milk.lifecycle_state == "ACTIVE"


def test_preview_makes_zero_writes(
    db_client: TestClient, db_session: Session
) -> None:
    owner, _op, bid = _bootstrap(db_client, db_session)
    sid = _create_source(owner, bid)
    _propose_and_activate(owner, bid, sid)
    resp = _preview(
        owner, bid, sid,
        _rows(("آب معدنی", 500, "W-1", "", ""), ("شیر", 1200, "M-1", "", "")),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["complete"] is True
    assert body["counts"]["new"] == 2
    assert body["counts"]["read"] == 2
    assert len(_products(db_session, bid)) == 0
    assert len(_runs(db_session, bid)) == 0


# --- entitlements ------------------------------------------------------------------------


def test_product_entitlement_limit_blocks_new_products(
    db_client: TestClient, db_session: Session
) -> None:
    owner, op, bid = _bootstrap(db_client, db_session)
    resp = op.post(
        "/api/v1/plans",
        json={"code": "tiny", "name": "Tiny", "price": 100_000, "product_limit": 2},
    )
    assert resp.status_code in (200, 201), resp.text
    sub = db_client.post(
        f"/api/v1/businesses/{bid}/subscriptions", json={"plan_code": "tiny"}
    ).json()
    resp = op.post(
        f"/api/v1/subscriptions/{sub['subscription_id']}/payments/verify",
        json={"reference": "IR-TINY"},
    )
    assert resp.status_code == 200, resp.text

    sid = _create_source(owner, bid)
    _propose_and_activate(owner, bid, sid)
    resp = _import(
        owner, bid, sid,
        _rows(("A", 1, "A-1", "", ""), ("B", 2, "B-1", "", ""), ("C", 3, "C-1", "", "")),
    )
    assert resp.status_code == 202, resp.text
    counts = resp.json()["counts"]
    assert counts["new"] == 2
    assert counts["blocked"] == 1
    assert len(_products(db_session, bid)) == 2


def test_source_entitlement_limit_blocks_second_source(
    db_client: TestClient, db_session: Session
) -> None:
    owner, op, bid = _bootstrap(db_client, db_session)
    resp = op.post(
        "/api/v1/plans",
        json={"code": "onesrc", "name": "One Source", "price": 100_000, "source_limit": 1},
    )
    assert resp.status_code in (200, 201), resp.text
    sub = db_client.post(
        f"/api/v1/businesses/{bid}/subscriptions", json={"plan_code": "onesrc"}
    ).json()
    resp = op.post(
        f"/api/v1/subscriptions/{sub['subscription_id']}/payments/verify",
        json={"reference": "IR-SRC"},
    )
    assert resp.status_code == 200, resp.text

    resp = owner.post(
        f"/api/v1/businesses/{bid}/sources", json={"name": "S1", "kind": "EXCEL_UPLOAD"}
    )
    assert resp.status_code == 201, resp.text
    resp = owner.post(
        f"/api/v1/businesses/{bid}/sources", json={"name": "S2", "kind": "EXCEL_UPLOAD"}
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "entitlement_denied"


def test_daily_sync_frequency_limit(
    db_client: TestClient, db_session: Session
) -> None:
    owner, op, bid = _bootstrap(db_client, db_session)
    resp = op.post(
        "/api/v1/plans",
        json={
            "code": "onedsync", "name": "One Sync", "price": 100_000,
            "sync_frequency_per_day": 1, "product_limit": 10,
        },
    )
    assert resp.status_code in (200, 201), resp.text
    sub = db_client.post(
        f"/api/v1/businesses/{bid}/subscriptions", json={"plan_code": "onedsync"}
    ).json()
    resp = op.post(
        f"/api/v1/subscriptions/{sub['subscription_id']}/payments/verify",
        json={"reference": "IR-SYNC"},
    )
    assert resp.status_code == 200, resp.text

    sid = _create_source(owner, bid)
    _propose_and_activate(owner, bid, sid)
    assert _import(owner, bid, sid, _rows(("A", 1, "A-1", "", ""))).status_code == 202
    resp = _import(owner, bid, sid, _rows(("A", 1, "A-1", "", "")))
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "entitlement_denied"


def test_source_creation_requires_active_subscription(
    db_client: TestClient, db_session: Session
) -> None:
    """No subscription -> the entitlement gate blocks source creation."""
    _register_login(db_client, "owner@example.com", "Owner")
    biz = db_client.post(
        "/api/v1/businesses", json={"name": "NoSub", "business_type_key": "general"}
    ).json()
    bid = biz["business_id"]
    resp = db_client.post(
        f"/api/v1/businesses/{bid}/sources", json={"name": "S1", "kind": "EXCEL_UPLOAD"}
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "entitlement_denied"


# --- isolation / permissions / audit --------------------------------------------------------


def test_cross_tenant_product_access_is_404(
    db_client: TestClient, db_session: Session
) -> None:
    owner, _op, bid = _bootstrap(db_client, db_session)
    sid = _create_source(owner, bid)
    _propose_and_activate(owner, bid, sid)
    assert _import(owner, bid, sid, _rows(("آب معدنی", 500, "W-1", "", ""))).status_code == 202
    prod = _product_by_sku(db_session, bid, "W-1")

    # A completely separate business owner.
    other = _client_for(db_client)
    _register_login(other, "other@example.com", "Other")
    other_biz = other.post(
        "/api/v1/businesses", json={"name": "OtherCo", "business_type_key": "general"}
    ).json()
    other_bid = other_biz["business_id"]

    assert (
        other.get(f"/api/v1/businesses/{other_bid}/products/{prod.product_id}").status_code
        == 404
    )
    assert (
        other.get(
            f"/api/v1/businesses/{other_bid}/sources/{sid}"
        ).status_code
        == 404
    )
    # Listing the other tenant's products returns none.
    assert other.get(f"/api/v1/businesses/{other_bid}/products").json() == []


def test_permission_denied_for_narrow_admin_profile(
    db_client: TestClient, db_session: Session
) -> None:
    owner, _op, bid = _bootstrap(db_client, db_session)
    sid = _create_source(owner, bid)
    _propose_and_activate(owner, bid, sid)

    # Admin member with a narrowed profile: view only.
    from app.infrastructure.db.models import Membership

    resp = owner.post(f"/api/v1/businesses/{bid}/invites", json={}).json()
    limited = _client_for(db_client)
    _register_login(limited, "limited@example.com", "Limited")
    req = limited.post(
        "/api/v1/admin-requests",
        json={"method": "invite_code", "code": resp["code"]},
    ).json()
    approve = owner.post(
        f"/api/v1/businesses/{bid}/admin-requests/{req['request_id']}/approve"
    ).json()
    membership = db_session.get(Membership, uuid.UUID(approve["membership_id"]))
    membership.permissions = ["products.view", "sources.view"]
    db_session.commit()

    # View works...
    assert limited.get(f"/api/v1/businesses/{bid}/products").status_code == 200
    # ...but import and mapping management are denied.
    assert (
        _import(limited, bid, sid, _rows(("A", 1, "A-1", "", ""))).status_code == 403
    )
    resp = limited.post(
        f"/api/v1/businesses/{bid}/sources/{sid}/mapping", json={"entries": _mapping_entries()}
    )
    assert resp.status_code == 403
    # Review cases also denied (products.review_mapping not granted).
    assert limited.get(f"/api/v1/businesses/{bid}/review-cases").status_code == 403


def test_audit_trail_for_import_and_product_edit(
    db_client: TestClient, db_session: Session
) -> None:
    owner, _op, bid = _bootstrap(db_client, db_session)
    sid = _create_source(owner, bid)
    _propose_and_activate(owner, bid, sid)
    assert _import(owner, bid, sid, _rows(("آب معدنی", 500, "W-1", "", ""))).status_code == 202
    prod = _product_by_sku(db_session, bid, "W-1")

    resp = owner.patch(
        f"/api/v1/businesses/{bid}/products/{prod.product_id}",
        json={"price": 550},
    )
    assert resp.status_code == 200, resp.text

    actions = list(
        db_session.scalars(
            select(AuditLog.action).where(AuditLog.business_id == uuid.UUID(bid))
        ).all()
    )
    assert "import.run.completed" in actions
    assert "product.updated" in actions
    assert "source.mapping.activated" in actions


def test_paused_source_cannot_import(
    db_client: TestClient, db_session: Session
) -> None:
    owner, _op, bid = _bootstrap(db_client, db_session)
    sid = _create_source(owner, bid)
    _propose_and_activate(owner, bid, sid)
    resp = owner.post(f"/api/v1/businesses/{bid}/sources/{sid}/pause")
    assert resp.status_code == 200, resp.text
    resp = _import(owner, bid, sid, _rows(("A", 1, "A-1", "", "")))
    assert resp.status_code == 409
