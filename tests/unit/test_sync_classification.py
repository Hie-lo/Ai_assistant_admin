from app.domain.sync_classification import (
    PublicationChangeAction,
    classify_publication_change,
)


def test_disabled_automatic_mode_has_no_side_effect_action():
    assert classify_publication_change(["PRICE_CHANGED"], can_edit=True) == PublicationChangeAction.NOOP


def test_no_change_is_noop():
    assert classify_publication_change([], can_edit=True, automatic_enabled=True) == PublicationChangeAction.NOOP


def test_editable_low_risk_change_is_edit():
    assert (
        classify_publication_change(
            ["PRICE_CHANGED"], can_edit=True, automatic_enabled=True
        )
        == PublicationChangeAction.EDIT
    )


def test_media_change_without_media_edit_is_repost():
    assert (
        classify_publication_change(
            ["MEDIA_CHANGED"], can_edit=True, automatic_enabled=True
        )
        == PublicationChangeAction.REPOST
    )


def test_identity_change_is_blocked():
    assert (
        classify_publication_change(
            ["IDENTITY_IDENTIFIER_CHANGED"], can_edit=True, automatic_enabled=True
        )
        == PublicationChangeAction.BLOCKED
    )


def test_unsupported_edit_falls_back_to_repost():
    assert (
        classify_publication_change(
            ["DESCRIPTION_CHANGED"], can_edit=False, automatic_enabled=True
        )
        == PublicationChangeAction.REPOST
    )
