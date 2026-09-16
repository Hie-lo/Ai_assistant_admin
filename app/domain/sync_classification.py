"""Deterministic Product-change to publication action classification."""
from __future__ import annotations

from enum import StrEnum


class PublicationChangeAction(StrEnum):
    NOOP = "NOOP"
    EDIT = "EDIT"
    REPOST = "REPOST"
    BLOCKED = "BLOCKED"


def classify_publication_change(
    categories: list[str] | tuple[str, ...],
    *,
    can_edit: bool,
    can_edit_media: bool = False,
    automatic_enabled: bool = False,
) -> PublicationChangeAction:
    """Choose a safe downstream action without performing side effects."""
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
