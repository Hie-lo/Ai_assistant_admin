"""Unit tests for the pure identity domain (normalization, fingerprint,
resolution priority + no-auto-merge guarantees)."""

from __future__ import annotations

import pytest
from app.domain import enums
from app.domain.identity import (
    IdentityCandidate,
    normalize_text,
    product_fingerprint,
    resolve_identity,
    text_fingerprint,
)

pytestmark = pytest.mark.unit


# --- normalization -------------------------------------------------------------


def test_normalize_persian_yae_variants() -> None:
    # Persian yae (U+06CC) vs Arabic yeh (U+064A) must normalize equal.
    persian_yae = "در\u06CCافت"
    arabic_yeh = "در\u064Aافت"
    assert persian_yae != arabic_yeh
    # Both must normalize to the same canonical form (Arabic yeh U+064A).
    canonical = "در\u064Aافت"
    assert normalize_text(persian_yae) == canonical
    assert normalize_text(arabic_yeh) == canonical


def test_normalize_persian_kaf_variants() -> None:
    # Persian kaf (U+06A9) vs Arabic kaf (U+0643) must normalize equal.
    persian_kaf = "\u06A9\u06CC\u0641\u062A"
    arabic_kaf = "\u0643\u064A\u0641\u062A"
    assert persian_kaf != arabic_kaf
    assert normalize_text(persian_kaf) == normalize_text(arabic_kaf)


def test_normalize_casefold_and_whitespace() -> None:
    assert normalize_text("  Hello   World ") == "hello world"
    assert normalize_text("ABC-123") == "abc-123"


def test_normalize_empty() -> None:
    assert normalize_text(None) == ""
    assert normalize_text("") == ""


def test_text_fingerprint_deterministic_and_sensitive() -> None:
    a = text_fingerprint("name", "cat", "spec")
    b = text_fingerprint("name", "cat", "spec")
    c = text_fingerprint("name", "cat", "other")
    assert a == b
    assert a != c
    assert len(a) == 64  # sha256 hex


# --- fingerprint composition -----------------------------------------------------


def test_fingerprint_name_only_is_prohibited() -> None:
    """A bare name can never form a confident fingerprint."""
    assert product_fingerprint(name="آب معدنی", category=None, spec_value=None) == ""


def test_fingerprint_name_plus_category() -> None:
    fp1 = product_fingerprint(name="آب معدنی", category="نوشیدنی", spec_value=None)
    fp2 = product_fingerprint(name="آب معدنی", category="نوشیدنی", spec_value=None)
    fp3 = product_fingerprint(name="آب معدنی", category="لوازم", spec_value=None)
    assert fp1 == fp2
    assert fp1 != fp3
    assert fp1 != ""


def test_fingerprint_uses_spec_value() -> None:
    fp = product_fingerprint(name="Laptop", category="PC", spec_value="16GB RAM")
    fp2 = product_fingerprint(name="Laptop", category="PC", spec_value="8GB RAM")
    assert fp != fp2


def test_fingerprint_description_fallback_truncated_to_120() -> None:
    long_desc = "x" * 300
    fp = product_fingerprint(name="Prod", category=None, spec_value=None, description=long_desc)
    fp_truncated = product_fingerprint(
        name="Prod", category=None, spec_value=None, description=long_desc[:120]
    )
    assert fp == fp_truncated  # only the first 120 normalized chars matter
    assert fp != ""


def test_fingerprint_normalizes_inputs() -> None:
    fp1 = product_fingerprint(name="  آب  معدنی ", category="نوشیدنی", spec_value=None)
    fp2 = product_fingerprint(name="آب معدنی", category="نوشیدنی", spec_value=None)
    assert fp1 == fp2


# --- resolution -------------------------------------------------------------------


def _cand(
    pid: str = "p1",
    external_id: str | None = None,
    sku: str | None = None,
    barcode: str | None = None,
    fingerprint: str | None = None,
    name: str | None = None,
) -> IdentityCandidate:
    return IdentityCandidate(
        product_id=pid,
        external_id=external_id,
        sku=sku,
        barcode=barcode,
        fingerprint=fingerprint,
        name_for_match=name,
    )


def test_exact_match_by_external_id() -> None:
    res = resolve_identity(
        external_id="E-1",
        sku=None,
        barcode=None,
        fingerprint=None,
        name="X",
        candidates=[_cand("p1", external_id="E-1"), _cand("p2", sku="S-9")],
    )
    assert res.outcome is enums.IdentityOutcome.EXACT_MATCH
    assert res.method == "external_id"
    assert res.matched_product_id == "p1"


def test_external_id_held_by_two_is_conflict() -> None:
    res = resolve_identity(
        external_id="E-1",
        sku=None,
        barcode=None,
        fingerprint=None,
        name="X",
        candidates=[_cand("p1", external_id="E-1"), _cand("p2", external_id="E-1")],
    )
    assert res.outcome is enums.IdentityOutcome.IDENTITY_CONFLICT
    assert res.matched_product_id is None


def test_confident_match_by_sku() -> None:
    res = resolve_identity(
        external_id=None,
        sku="S-1",
        barcode=None,
        fingerprint=None,
        name="X",
        candidates=[_cand("p1", sku="s-1 ")],
    )
    assert res.outcome is enums.IdentityOutcome.CONFIDENT_MATCH
    assert res.method == "sku"
    assert res.matched_product_id == "p1"


def test_sku_matching_two_is_ambiguous_never_merges() -> None:
    res = resolve_identity(
        external_id=None,
        sku="S-1",
        barcode=None,
        fingerprint=None,
        name="X",
        candidates=[_cand("p1", sku="S-1"), _cand("p2", sku="S-1")],
    )
    assert res.outcome is enums.IdentityOutcome.AMBIGUOUS
    assert res.matched_product_id is None


def test_confident_match_by_barcode() -> None:
    res = resolve_identity(
        external_id=None, sku=None, barcode="6281001234567", fingerprint=None, name="X",
        candidates=[_cand("p1", barcode="6281001234567")],
    )
    assert res.outcome is enums.IdentityOutcome.CONFIDENT_MATCH
    assert res.method == "barcode"


def test_confident_match_by_fingerprint() -> None:
    res = resolve_identity(
        external_id=None, sku=None, barcode=None, fingerprint="fp-1", name="X",
        candidates=[_cand("p1", fingerprint="fp-1"), _cand("p2", name="X")],
    )
    assert res.outcome is enums.IdentityOutcome.CONFIDENT_MATCH
    assert res.method == "fingerprint"
    assert res.matched_product_id == "p1"


def test_name_only_collision_is_possible_duplicate_not_match() -> None:
    """Name collision without identity evidence must never auto-match."""
    res = resolve_identity(
        external_id=None, sku=None, barcode=None, fingerprint=None, name="آب معدنی",
        candidates=[_cand("p1", name="آب معدنی")],
    )
    assert res.outcome is enums.IdentityOutcome.POSSIBLE_DUPLICATE
    assert res.matched_product_id is None


def test_no_evidence_is_new_product() -> None:
    res = resolve_identity(
        external_id=None, sku=None, barcode=None, fingerprint=None, name="Brand New",
        candidates=[_cand("p1", name="Something else")],
    )
    assert res.outcome is enums.IdentityOutcome.NEW_PRODUCT


def test_external_id_not_held_falls_through_to_sku() -> None:
    res = resolve_identity(
        external_id="E-NEW",
        sku="S-1",
        barcode=None,
        fingerprint=None,
        name="X",
        candidates=[_cand("p1", sku="S-1")],
    )
    assert res.outcome is enums.IdentityOutcome.CONFIDENT_MATCH
    assert res.method == "sku"


def test_external_id_beats_sku_priority() -> None:
    """External ID has priority over SKU when both are present and unique."""
    res = resolve_identity(
        external_id="E-1",
        sku="S-2",
        barcode=None,
        fingerprint=None,
        name="X",
        candidates=[_cand("p1", external_id="E-1"), _cand("p2", sku="S-2")],
    )
    assert res.outcome is enums.IdentityOutcome.EXACT_MATCH
    assert res.matched_product_id == "p1"
