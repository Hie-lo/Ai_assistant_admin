"""Unit tests for change risk classification (owner-approved thresholds)."""

from __future__ import annotations

import pytest
from app.domain import enums
from app.domain.change_risk import (
    MASS_MISSING_RATIO,
    SUSPICIOUS_MASS_CHANGE_COUNT,
    SUSPICIOUS_PRICE_JUMP_RATIO,
    field_risk,
    max_risk,
)

pytestmark = pytest.mark.unit

CORE = enums.FieldKind.CORE
CUSTOM = enums.FieldKind.CUSTOM


def test_low_risk_fields() -> None:
    assert field_risk("name", kind=CORE, old_value="a", new_value="b") is enums.ChangeRisk.LOW
    assert (
        field_risk("description", kind=CORE, old_value="a", new_value="b")
        is enums.ChangeRisk.LOW
    )
    assert field_risk("stock", kind=CORE, old_value=1, new_value=2) is enums.ChangeRisk.LOW


def test_price_small_change_is_medium() -> None:
    assert (
        field_risk("price", kind=CORE, old_value=1000, new_value=1100) is enums.ChangeRisk.MEDIUM
    )


def test_price_jump_over_50_percent_is_high() -> None:
    assert (
        field_risk("price", kind=CORE, old_value=1000, new_value=1600) is enums.ChangeRisk.HIGH
    )
    assert (
        field_risk("price", kind=CORE, old_value=1000, new_value=400) is enums.ChangeRisk.HIGH
    )


def test_price_from_zero_is_high() -> None:
    assert field_risk("price", kind=CORE, old_value=0, new_value=100) is enums.ChangeRisk.HIGH


def test_category_and_currency_medium() -> None:
    assert (
        field_risk("category", kind=CORE, old_value="a", new_value="b") is enums.ChangeRisk.MEDIUM
    )
    assert (
        field_risk("currency", kind=CORE, old_value="IRT", new_value="USD")
        is enums.ChangeRisk.MEDIUM
    )


def test_identity_fields_always_critical() -> None:
    for f in ("external_id", "sku", "barcode"):
        assert field_risk(f, kind=CORE, old_value="a", new_value="b") is enums.ChangeRisk.CRITICAL


def test_custom_fields_default_high() -> None:
    assert (
        field_risk("battery_capacity", kind=CUSTOM, old_value="1", new_value="2")
        is enums.ChangeRisk.HIGH
    )


def test_max_risk_ordering() -> None:
    assert (
        max_risk(
            [
                enums.ChangeRisk.LOW,
                enums.ChangeRisk.MEDIUM,
                enums.ChangeRisk.HIGH,
            ]
        )
        is enums.ChangeRisk.HIGH
    )
    assert max_risk([]) is enums.ChangeRisk.LOW
    assert (
        max_risk([enums.ChangeRisk.LOW, enums.ChangeRisk.CRITICAL]) is enums.ChangeRisk.CRITICAL
    )


def test_thresholds_match_approved_design() -> None:
    assert SUSPICIOUS_PRICE_JUMP_RATIO == 0.5
    assert SUSPICIOUS_MASS_CHANGE_COUNT == 5
    assert MASS_MISSING_RATIO == 0.5
