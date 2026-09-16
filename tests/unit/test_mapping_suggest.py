"""Unit tests for the mapping suggestion heuristics (Persian + English)."""

from __future__ import annotations

import pytest
from app.application.mapping import CORE_FIELDS, suggest_entries

pytestmark = pytest.mark.unit


def _by_column(suggested):
    return {s.column: s for s in suggested}


def test_persian_headers_mapped() -> None:
    headers = ["نام", "قیمت", "توضیحات", "موجودی", "دسته", "کد کالا", "بارکد", "تصویر"]
    out = _by_column(suggest_entries(headers))
    assert out["نام"].canonical_field == "name"
    assert out["قیمت"].canonical_field == "price"
    assert out["توضیحات"].canonical_field == "description"
    assert out["موجودی"].canonical_field == "stock"
    assert out["دسته"].canonical_field == "category"
    assert out["کد کالا"].canonical_field == "sku"
    assert out["بارکد"].canonical_field == "barcode"
    assert out["تصویر"].canonical_field == "image1"
    for s in out.values():
        assert s.field_kind == "CORE"
        assert s.confidence == 0.9


def test_english_headers_mapped() -> None:
    headers = [
        "Product Name", "Price (Toman)", "Description", "Stock",
        "Category", "SKU", "Barcode", "Main Image",
    ]
    out = _by_column(suggest_entries(headers))
    assert out["Product Name"].canonical_field == "name"
    assert out["Price (Toman)"].canonical_field == "price"
    assert out["Description"].canonical_field == "description"
    assert out["Stock"].canonical_field == "stock"
    assert out["Category"].canonical_field == "category"
    assert out["SKU"].canonical_field == "sku"
    assert out["Barcode"].canonical_field == "barcode"
    assert out["Main Image"].canonical_field == "image1"


def test_unmapped_column_becomes_custom() -> None:
    out = _by_column(suggest_entries(["Name", "Weight", "Notes"]))
    assert out["Name"].canonical_field == "name"
    assert out["Weight"].canonical_field is None
    assert out["Weight"].field_kind == "CUSTOM"
    assert out["Weight"].confidence == 0.0
    assert out["Notes"].field_kind == "CUSTOM"


def test_duplicate_alias_columns_do_not_double_map() -> None:
    """Two columns matching the same core field: first wins, second is custom."""
    out = _by_column(suggest_entries(["Name", "Title", "Description"]))
    assert out["Name"].canonical_field == "name"
    assert out["Title"].canonical_field is None  # 'name' already taken
    assert out["Title"].field_kind == "CUSTOM"


def test_name_column_is_required_in_meta() -> None:
    meta = CORE_FIELDS["name"]
    assert meta["required"] is True
    assert meta["type"] == "STRING"


def test_image_slots_map_to_media_url_type() -> None:
    for key in ("image1", "image2", "image3"):
        assert CORE_FIELDS[key]["type"] == "MEDIA_URL"
