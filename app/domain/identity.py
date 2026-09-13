"""Pure identity logic for Product (owner-approved algorithm, 2026-09-13).

Sources: PRODUCT_DOMAIN_SPECIFICATION_V2 sections 3, 30, 31 and
SOURCE_SYNC_DOMAIN_SPECIFICATION_V1 sections 10-14.

Rules (deterministic, explainable, no AI in the identity path):
- Priority: explicit customer ID -> SKU -> barcode -> deterministic
  fingerprint -> (no evidence) new product.
- Name-only matching is PROHIBITED as a confident identity rule.
- AMBIGUOUS / IDENTITY_CONFLICT never auto-merge (false merge is more
  dangerous than a false-new-product).
- Evidence is compact and stored for audit.
"""

from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass, field

from app.domain.enums import IdentityOutcome

# --- Normalization -----------------------------------------------------------

#: Persian letter unification (yae/kaf variants) applied before folding.
_PERSIAN_VARIANTS = str.maketrans({"ی": "ي", "ک": "ك", "گ": "گ", "‌": " "})


def normalize_text(value: str | None) -> str:
    """Canonical text form: NFKC, Persian variants, casefold, whitespace.

    Deterministic across reads so it can seed fingerprints and equality.
    """
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", value)
    text = text.translate(_PERSIAN_VARIANTS)
    text = text.casefold()
    text = " ".join(text.split())
    return text


def text_fingerprint(*parts: str | None) -> str:
    """Stable SHA-256 fingerprint of normalized parts."""
    joined = "\u0001".join(normalize_text(p) for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


# --- Fingerprint composition (approved: name + category + first spec) --------


def product_fingerprint(
    *,
    name: str | None,
    category: str | None,
    spec_value: str | None,
    description: str | None = None,
) -> str:
    """Deterministic identity fingerprint (approved composition).

    - With category/spec:  name + category + first technical spec.
    - Without:             name + first 120 normalized chars of description.
    Never name-only: a bare name can never form a confident match by itself.
    """
    n_name = normalize_text(name)
    if not n_name:
        return ""
    if category or spec_value:
        return text_fingerprint(name, category, spec_value)
    n_desc = normalize_text(description)
    if not n_desc:
        return ""  # name-only: NOT a fingerprint (prohibited as confident rule)
    return text_fingerprint(name, n_desc[:120])


# --- Resolution ----------------------------------------------------------------


@dataclass(frozen=True)
class IdentityEvidence:
    """Compact explainable evidence for an identity decision."""

    outcome: IdentityOutcome
    method: str | None = None  # "external_id" | "sku" | "barcode" | "fingerprint" | None
    matched_product_id: str | None = None
    detail: str = ""
    extra: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class IdentityCandidate:
    """An existing product considered during resolution (id + identifiers)."""

    product_id: str
    external_id: str | None
    sku: str | None
    barcode: str | None
    fingerprint: str | None
    name_for_match: str | None = None


def resolve_identity(
    *,
    external_id: str | None,
    sku: str | None,
    barcode: str | None,
    fingerprint: str | None,
    name: str | None,
    candidates: list[IdentityCandidate],
) -> IdentityEvidence:
    """Resolve a source row to an existing product (or not).

    ``candidates`` = all non-archived products of the business (the caller
    scopes this; cross-business leakage is impossible by construction).
    """
    n_ext = normalize_text(external_id)
    n_sku = normalize_text(sku)
    n_bar = normalize_text(barcode)

    # 1. Explicit stable customer ID with validated continuity.
    if n_ext:
        holders = [
            c
            for c in candidates
            if normalize_text(c.external_id) and normalize_text(c.external_id) == n_ext
        ]
        if len(holders) == 1:
            return IdentityEvidence(
                outcome=IdentityOutcome.EXACT_MATCH,
                method="external_id",
                matched_product_id=holders[0].product_id,
                detail=f"external_id '{external_id}'",
            )
        if len(holders) > 1:
            return IdentityEvidence(
                outcome=IdentityOutcome.IDENTITY_CONFLICT,
                method="external_id",
                detail=f"external_id '{external_id}' held by {len(holders)} products",
                extra={"holder_ids": [h.product_id for h in holders]},
            )
        # ID present but held by no product: fall through (identifier change
        # vs new product is decided by the later steps; a known product whose
        # ID *changed* surfaces as CRITICAL change detection, not here).

    # 2. Reliable source/external keys: SKU then barcode.
    if n_sku:
        matches = [c for c in candidates if normalize_text(c.sku) == n_sku]
        if len(matches) == 1:
            return IdentityEvidence(
                outcome=IdentityOutcome.CONFIDENT_MATCH,
                method="sku",
                matched_product_id=matches[0].product_id,
                detail=f"sku '{sku}'",
            )
        if len(matches) > 1:
            return IdentityEvidence(
                outcome=IdentityOutcome.AMBIGUOUS,
                method="sku",
                detail=f"sku '{sku}' matches {len(matches)} products",
                extra={"candidate_ids": [m.product_id for m in matches]},
            )
    if n_bar:
        matches = [c for c in candidates if normalize_text(c.barcode) == n_bar]
        if len(matches) == 1:
            return IdentityEvidence(
                outcome=IdentityOutcome.CONFIDENT_MATCH,
                method="barcode",
                matched_product_id=matches[0].product_id,
                detail=f"barcode '{barcode}'",
            )
        if len(matches) > 1:
            return IdentityEvidence(
                outcome=IdentityOutcome.AMBIGUOUS,
                method="barcode",
                detail=f"barcode '{barcode}' matches {len(matches)} products",
                extra={"candidate_ids": [m.product_id for m in matches]},
            )

    # 3. Deterministic composite fingerprint.
    if fingerprint:
        matches = [c for c in candidates if c.fingerprint and c.fingerprint == fingerprint]
        if len(matches) == 1:
            return IdentityEvidence(
                outcome=IdentityOutcome.CONFIDENT_MATCH,
                method="fingerprint",
                matched_product_id=matches[0].product_id,
                detail="deterministic fingerprint match",
            )
        if len(matches) > 1:
            return IdentityEvidence(
                outcome=IdentityOutcome.AMBIGUOUS,
                method="fingerprint",
                detail="fingerprint matches multiple products",
                extra={"candidate_ids": [m.product_id for m in matches]},
            )

    # 4. No confident evidence. Name collision (without other evidence) is a
    #    duplicate candidate for review — never an auto match.
    n_name = normalize_text(name)
    if n_name:
        same_name = [
            c for c in candidates if normalize_text(c.name_for_match) == n_name
        ]
        if same_name:
            return IdentityEvidence(
                outcome=IdentityOutcome.POSSIBLE_DUPLICATE,
                method=None,
                detail=(
                    f"name '{name}' matches {len(same_name)} existing "
                    "product(s) without identity evidence"
                ),
                extra={"candidate_ids": [m.product_id for m in same_name]},
            )

    return IdentityEvidence(outcome=IdentityOutcome.NEW_PRODUCT, method=None)
