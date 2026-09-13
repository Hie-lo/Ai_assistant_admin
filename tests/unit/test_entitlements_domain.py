"""Pure entitlement domain logic: limits, usage registry, feature flags."""

from __future__ import annotations

import uuid

import pytest
from app.domain.entitlements import (
    Entitlements,
    check_limit,
    empty_entitlements,
    feature_enabled,
    register_usage_counter,
    usage_count,
)


def test_no_subscription_means_no_active_entitlement() -> None:
    ent = empty_entitlements()
    assert ent.has_active is False
    assert ent.subscription_status is None
    assert ent.plan_code is None


def test_check_limit_unlimited_when_none() -> None:
    ent = empty_entitlements()
    assert check_limit(ent, "products", 0) is True
    assert check_limit(ent, "products", 10_000) is True


def test_check_limit_blocks_at_and_above_limit() -> None:
    ent = Entitlements(
        has_active=True, subscription_status="ACTIVE", plan_code="starter", product_limit=100
    )
    assert check_limit(ent, "products", 99) is True
    assert check_limit(ent, "products", 100) is False  # at the limit -> blocked
    assert check_limit(ent, "products", 101) is False


def test_check_limit_all_keys_map_to_plan_attributes() -> None:
    ent = Entitlements(
        has_active=True,
        subscription_status="ACTIVE",
        plan_code="starter",
        product_limit=1,
        source_limit=2,
        channel_limit=3,
        admin_seat_limit=4,
        media_storage_limit_bytes=5,
    )
    assert check_limit(ent, "products", 0) is True
    assert check_limit(ent, "products", 1) is False
    assert check_limit(ent, "sources", 2) is False
    assert check_limit(ent, "channels", 3) is False
    assert check_limit(ent, "admin_seats", 4) is False
    assert check_limit(ent, "media_storage", 5) is False


def test_usage_count_defaults_to_zero_when_unregistered() -> None:
    # No counter registered for this key in this process.
    assert usage_count("media_storage", db=None, business_id=uuid.uuid4()) == 0


def test_usage_count_uses_registered_counter() -> None:
    register_usage_counter("sources", lambda db, business_id: 42)
    try:
        assert usage_count("sources", db=None, business_id=uuid.uuid4()) == 42
    finally:
        # keep the process registry clean for other test runs
        from app.domain import entitlements as d

        d._usage_counters.pop("sources", None)


def test_register_unknown_limit_key_rejected() -> None:
    with pytest.raises(ValueError):
        register_usage_counter("not_a_key", lambda db, business_id: 0)


def test_feature_flag_enabled_only_when_set() -> None:
    ent = Entitlements(
        has_active=True,
        subscription_status="ACTIVE",
        plan_code="starter",
        feature_flags={"beta_reports": True},
    )
    assert feature_enabled(ent, "beta_reports") is True
    assert feature_enabled(ent, "other_flag") is False
