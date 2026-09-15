"""Change risk classification (approved baseline, 2026-09-13).

Spec: PRODUCT_DOMAIN_SPECIFICATION_V2 sections 15-16. Risk determines
validation/review behavior; it does not by itself block changes.

V1 deterministic rules (owner-approved):
- identity identifiers (external_id/sku/barcode) changed  -> CRITICAL (always
  review; the old identifier is retained, never auto-merged)
- technical/custom specification fields                   -> HIGH
- price changed by more than 50%                           -> HIGH (suspicious)
- price / category                                         -> MEDIUM
- name / description / stock                               -> LOW
"""

from __future__ import annotations

from app.domain import enums

#: Core field -> base risk.
CORE_FIELD_RISK: dict[str, enums.ChangeRisk] = {
    "name": enums.ChangeRisk.LOW,
    "description": enums.ChangeRisk.LOW,
    "stock": enums.ChangeRisk.LOW,
    "price": enums.ChangeRisk.MEDIUM,
    "currency": enums.ChangeRisk.MEDIUM,
    "category": enums.ChangeRisk.MEDIUM,
}

IDENTITY_FIELDS: frozenset[str] = frozenset({"external_id", "sku", "barcode"})

#: Custom attribute fields are technical specs unless the mapping says LOW.
CUSTOM_FIELD_DEFAULT_RISK = enums.ChangeRisk.HIGH

#: Price relative change above this ratio is suspicious (owner-approved 50%).
SUSPICIOUS_PRICE_JUMP_RATIO = 0.5

#: The same HIGH-risk attribute changing on at least this many products in
#: one run is a suspicious mass change (owner-approved 5).
SUSPICIOUS_MASS_CHANGE_COUNT = 5

#: A single confirmed read may not mark more than this fraction of the
#: baseline active products missing (owner-approved 50%).
MASS_MISSING_RATIO = 0.5


def field_risk(
    field_name: str,
    *,
    kind: enums.FieldKind,
    old_value: object = None,
    new_value: object = None,
) -> enums.ChangeRisk:
    """Risk of a single field change (field + business-type aware via kind)."""
    if field_name in IDENTITY_FIELDS:
        return enums.ChangeRisk.CRITICAL
    if kind is enums.FieldKind.CORE:
        risk = CORE_FIELD_RISK.get(field_name, enums.ChangeRisk.MEDIUM)
        if field_name == "price" and _price_jump_ratio(old_value, new_value) > (
            SUSPICIOUS_PRICE_JUMP_RATIO
        ):
            return enums.ChangeRisk.HIGH
        return risk
    return CUSTOM_FIELD_DEFAULT_RISK


def _price_jump_ratio(old_value: object, new_value: object) -> float:
    try:
        old = float(old_value)  # type: ignore[arg-type]
        new = float(new_value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    if old <= 0:
        return 1.0 if new != old else 0.0
    return abs(new - old) / old


def max_risk(risks: list[enums.ChangeRisk]) -> enums.ChangeRisk:
    order = [
        enums.ChangeRisk.LOW,
        enums.ChangeRisk.MEDIUM,
        enums.ChangeRisk.HIGH,
        enums.ChangeRisk.CRITICAL,
    ]
    return max(risks, key=order.index) if risks else enums.ChangeRisk.LOW
