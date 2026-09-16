"""Deterministic Product-change to publication action classification."""

from __future__ import annotations

from enum import StrEnum


class PublicationChangeAction(StrEnum):
    NOOP = "NOOP"
    EDIT = "EDIT"
    REPOST = "REPOST"
    BLOCKED = "BLOCKED"
    NEEDS_REVIEW = "NEEDS_REVIEW"


def classify_publication_change(
    categories: list[str] | tuple[str, ...],
    *,
    can_edit: bool,
    can_edit_media: bool = False,
    automatic_enabled: bool = False,
) -> PublicationChangeAction:
    """Choose a safe downstream action without performing side effects.

    This is the basic V1 classifier (manual mode + simple automatic).
    For hybrid automatic mode with risk awareness, use
    `classify_with_risk`.
    """
    if not automatic_enabled:
        return PublicationChangeAction.NOOP
    if not categories:
        return PublicationChangeAction.NOOP
    category_set = set(categories)
    if "IDENTITY_IDENTIFIER_CHANGED" in category_set:
        return PublicationChangeAction.BLOCKED
    if "MEDIA_CHANGED" in category_set and not can_edit_media:
        return PublicationChangeAction.REPOST
    if can_edit:
        return PublicationChangeAction.EDIT
    return PublicationChangeAction.REPOST


def classify_with_risk(
    categories: list[str] | tuple[str, ...],
    *,
    risk: str | None = None,
    can_edit: bool = True,
    can_edit_media: bool = False,
    automatic_enabled: bool = False,
) -> PublicationChangeAction:
    """Hybrid classifier: only LOW/MEDIUM auto, HIGH/CRITICAL needs review.

    Owner decision 2026-09-16 hybrid: low/medium changes auto-edit,
    high/critical + identity changes require manual review.
    """
    if not automatic_enabled:
        return PublicationChangeAction.NOOP
    if not categories:
        return PublicationChangeAction.NOOP

    category_set = set(categories)

    # Identity changes are always blocked for auto
    if "IDENTITY_IDENTIFIER_CHANGED" in category_set:
        return PublicationChangeAction.BLOCKED

    # Risk-based gating for hybrid mode
    if risk in ("HIGH", "CRITICAL"):
        return PublicationChangeAction.NEEDS_REVIEW

    # Media change without media edit capability -> repost needed
    # For hybrid, repost is considered high-impact, so needs review
    if "MEDIA_CHANGED" in category_set:
        if not can_edit_media:
            return PublicationChangeAction.NEEDS_REVIEW
        # Even with media edit, media changes are more visible -> review
        # for safety unless risk is LOW
        if risk not in ("LOW", "MEDIUM", None):
            return PublicationChangeAction.NEEDS_REVIEW

    # LOW/MEDIUM: safe to auto-edit if platform supports edit
    if can_edit:
        return PublicationChangeAction.EDIT

    # If edit not supported, repost would be needed -> needs review in hybrid
    return PublicationChangeAction.NEEDS_REVIEW


def should_auto_update(
    *,
    risk: str | None,
    categories: list[str] | None = None,
    automatic_enabled: bool = False,
) -> bool:
    """Whether a changed product should be auto-updated in hybrid mode."""
    if not automatic_enabled:
        return False
    if risk in ("HIGH", "CRITICAL"):
        return False
    if categories and "IDENTITY_IDENTIFIER_CHANGED" in categories:
        return False
    # Only LOW/MEDIUM or no risk specified
    return risk in ("LOW", "MEDIUM", None)
