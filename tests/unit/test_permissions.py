"""Permission matrix resolution (pure logic, no DB)."""

from __future__ import annotations

from app.domain.enums import MembershipRole
from app.domain.permissions import (
    ADMIN_MANAGE,
    ALL_PERMISSIONS,
    OWNER_ONLY_PERMISSIONS,
    admin_default_profile,
    owner_profile,
    resolve_profile,
)


def test_owner_has_every_permission() -> None:
    assert owner_profile() == ALL_PERMISSIONS


def test_admin_default_excludes_owner_only() -> None:
    profile = admin_default_profile()
    assert profile.isdisjoint(OWNER_ONLY_PERMISSIONS)
    assert profile == ALL_PERMISSIONS - OWNER_ONLY_PERMISSIONS
    assert ADMIN_MANAGE not in profile


def test_resolve_owner_ignores_granted_list() -> None:
    assert resolve_profile(MembershipRole.OWNER, ["products.view"]) == ALL_PERMISSIONS


def test_resolve_admin_strips_owner_only_from_granted() -> None:
    """A corrupt/over-granted stored list can never escalate to owner rights."""
    granted = sorted(ALL_PERMISSIONS)  # everything, including owner-only
    resolved = resolve_profile(MembershipRole.ADMIN, granted)
    assert resolved.isdisjoint(OWNER_ONLY_PERMISSIONS)


def test_resolve_admin_none_granted_uses_default() -> None:
    assert resolve_profile(MembershipRole.ADMIN, None) == admin_default_profile()


def test_owner_only_set_is_stable_subset() -> None:
    assert OWNER_ONLY_PERMISSIONS.issubset(ALL_PERMISSIONS)
    assert len(OWNER_ONLY_PERMISSIONS) == 7
