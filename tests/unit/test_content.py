"""Unit tests for the Phase 4 content engine (tokens, validation, render)."""

from __future__ import annotations

import pytest
from app.domain.content import (
    PRIORITY_AI,
    PRIORITY_CONTACT,
    PRIORITY_DECORATIVE,
    PRIORITY_IDENTITY,
    PRIORITY_PRICE_STOCK,
    ContentBlock,
    ContentError,
    RenderContext,
    block_priority,
    render_blocks,
    validate_blocks,
)
from app.domain.enums import BlockOwnership, BlockType

pytestmark = pytest.mark.unit

AI_KEYS = frozenset({"ai_description", "ai_short_title"})


def _ctx(**kw) -> RenderContext:
    base = dict(
        product_fields={
            "name": "Laptop X",
            "price": "1000",
            "currency": "IRT",
            "stock": "3",
            "category": "electronics",
            "description": "A fast laptop.",
            "hashtags": "#tech",
        },
        attributes={"color": "black"},
        ai_outputs={"ai_description": "Great machine."},
        media_urls=["https://x/1.jpg", "https://x/2.jpg"],
    )
    base.update(kw)
    return RenderContext(**base)


def _blocks(*specs: tuple[str, BlockType, dict]) -> list[ContentBlock]:
    return [
        ContentBlock(
            block_id=bid,
            type=btype,
            ownership=BlockOwnership.SYSTEM_MANAGED,
            payload=payload,
        )
        for bid, btype, payload in specs
    ]


# --- token validation (allowlist) ---


def test_validate_blocks_rejects_unknown_token() -> None:
    with pytest.raises(ContentError):
        validate_blocks(
            [
                {
                    "id": "a",
                    "type": "STATIC_TEXT",
                    "ownership": "STATIC",
                    "payload": {"text": "hi {unknown_field}"},
                }
            ],
            AI_KEYS,
        )


def test_validate_blocks_rejects_unknown_ai_key() -> None:
    with pytest.raises(ContentError):
        validate_blocks(
            [
                {
                    "id": "a",
                    "type": "STATIC_TEXT",
                    "ownership": "STATIC",
                    "payload": {"text": "hi {ai_nope}"},
                }
            ],
            AI_KEYS,
        )


def test_validate_blocks_allows_known_tokens_and_attr() -> None:
    blocks = [
        {
            "id": "a",
            "type": "STATIC_TEXT",
            "ownership": "STATIC",
            "payload": {"text": "{name} {price} {attr.color} {ai_description}"},
        }
    ]
    parsed = validate_blocks(blocks, AI_KEYS)
    assert len(parsed) == 1


def test_validate_blocks_rejects_duplicate_ids_and_empty() -> None:
    with pytest.raises(ContentError):
        validate_blocks([], AI_KEYS)
    dup = [
        {"id": "a", "type": "SEPARATOR", "ownership": "STATIC", "payload": {"text": "-"}},
        {"id": "a", "type": "SEPARATOR", "ownership": "STATIC", "payload": {"text": "-"}},
    ]
    with pytest.raises(ContentError):
        validate_blocks(dup, AI_KEYS)


def test_validate_blocks_rejects_bad_type_and_ownership() -> None:
    with pytest.raises(ContentError):
        ContentBlock.from_dict({"id": "a", "type": "BOGUS", "ownership": "STATIC"})
    with pytest.raises(ContentError):
        ContentBlock.from_dict({"id": "a", "type": "SEPARATOR", "ownership": "BOGUS"})


# --- block priority ---


def test_block_priority_mapping() -> None:
    name = _blocks(("n", BlockType.PRODUCT_FIELD, {"field": "name"}))[0]
    price = _blocks(("p", BlockType.PRODUCT_FIELD, {"field": "price"}))[0]
    stock = _blocks(("s", BlockType.PRODUCT_FIELD, {"field": "stock"}))[0]
    other = _blocks(("o", BlockType.PRODUCT_FIELD, {"field": "description"}))[0]
    contact = _blocks(("c", BlockType.CONTACT, {"text": "call us"}))[0]
    ai = _blocks(("a", BlockType.AI_OUTPUT, {"key": "ai_description"}))[0]
    tags = _blocks(("t", BlockType.HASHTAG_SET, {}))[0]
    static = _blocks(("x", BlockType.STATIC_TEXT, {"text": "hi"}))[0]
    assert block_priority(name) == PRIORITY_IDENTITY
    assert block_priority(price) == PRIORITY_PRICE_STOCK
    assert block_priority(stock) == PRIORITY_PRICE_STOCK
    assert block_priority(contact) == PRIORITY_CONTACT
    assert block_priority(ai) == PRIORITY_AI
    assert block_priority(tags) == 6
    assert block_priority(static) == PRIORITY_DECORATIVE
    assert block_priority(other) == 4


# --- rendering: token substitution + injection safety ---


def test_render_substitutes_product_and_ai_tokens() -> None:
    blocks = _blocks(
        ("n", BlockType.PRODUCT_FIELD, {"field": "name"}),
        ("d", BlockType.AI_OUTPUT, {"key": "ai_description"}),
    )
    result = render_blocks(blocks, _ctx())
    assert "Laptop X" in result.text
    assert "Great machine." in result.text


def test_render_injection_value_not_reparsed() -> None:
    # A product value that LOOKS like a token must be treated as inert data.
    ctx = _ctx(
        product_fields={"name": "{price} {name}", "price": "5", "currency": "IRT"}
    )
    blocks = _blocks(
        ("n", BlockType.PRODUCT_FIELD, {"field": "name"}),
        ("p", BlockType.PRODUCT_FIELD, {"field": "price"}),
    )
    result = render_blocks(blocks, ctx)
    # The literal name is printed verbatim; it does not expand recursively.
    assert result.text == "{price} {name}\n5"


def test_render_missing_optional_ai_is_omitted_with_warning() -> None:
    ctx = _ctx(ai_outputs={})
    blocks = _blocks(
        ("n", BlockType.PRODUCT_FIELD, {"field": "name"}),
        ("a", BlockType.AI_OUTPUT, {"key": "ai_description"}),
    )
    result = render_blocks(blocks, ctx)
    assert "Great machine." not in result.text
    assert any("ai_description" in w for w in result.warnings)


def test_render_custom_field_block() -> None:
    blocks = _blocks(
        ("c", BlockType.CUSTOM_FIELD, {"key": "color", "display_name": "color"}),
    )
    result = render_blocks(blocks, _ctx())
    assert "black" in result.text
    assert "color" in result.text


def test_render_media_reference_block() -> None:
    blocks = _blocks(("m", BlockType.MEDIA_REFERENCE, {}))
    result = render_blocks(blocks, _ctx())
    assert "2 item(s)" in result.text


def test_render_full_composition_under_limit_fits() -> None:
    blocks = _blocks(
        ("n", BlockType.PRODUCT_FIELD, {"field": "name"}),
        ("p", BlockType.PRODUCT_FIELD, {"field": "price"}),
        ("c", BlockType.CONTACT, {"text": "call us"}),
        ("a", BlockType.AI_OUTPUT, {"key": "ai_description"}),
    )
    result = render_blocks(blocks, _ctx(max_length=4096))
    assert result.fits is True
    assert result.blocked_reason is None
    assert result.total_chars == len(result.text)


# --- length handling: priority-ordered trimming ---


def test_render_trims_lowest_priority_first_when_over_limit() -> None:
    # Force a tiny limit so trimming kicks in.
    ctx = _ctx(max_length=30)
    blocks = _blocks(
        ("n", BlockType.PRODUCT_FIELD, {"field": "name"}),  # priority 1
        ("c", BlockType.CONTACT, {"text": "call us now"}),  # priority 3
        ("a", BlockType.AI_OUTPUT, {"key": "ai_description"}),  # priority 5
        ("t", BlockType.HASHTAG_SET, {}),  # priority 6
        ("x", BlockType.STATIC_TEXT, {"text": "decorative filler"}),  # priority 7
    )
    result = render_blocks(blocks, ctx)
    by_id = {b.block_id: b for b in result.blocks}
    # Identity + contact survive; decorative is dropped first.
    assert by_id["n"].kept is True
    assert by_id["c"].kept is True
    assert by_id["x"].kept is False
    # total within limit after trimming
    assert result.total_chars <= ctx.max_length


def test_render_blocks_when_only_essentials_still_too_long() -> None:
    # Make even the essentials overflow the limit.
    ctx = _ctx(max_length=5, product_fields={"name": "VERYLONGNAME", "price": "1"})
    blocks = _blocks(
        ("n", BlockType.PRODUCT_FIELD, {"field": "name"}),
        ("p", BlockType.PRODUCT_FIELD, {"field": "price"}),
    )
    result = render_blocks(blocks, ctx)
    assert result.fits is False
    assert result.blocked_reason is not None
    # essentials are never silently dropped
    by_id = {b.block_id: b for b in result.blocks}
    assert by_id["n"].kept is True
    assert by_id["p"].kept is True


def test_render_empty_block_text_is_skipped_in_assembly() -> None:
    ctx = _ctx(ai_outputs={})
    blocks = _blocks(
        ("a", BlockType.AI_OUTPUT, {"key": "ai_description"}),
        ("n", BlockType.PRODUCT_FIELD, {"field": "name"}),
    )
    result = render_blocks(blocks, ctx)
    # No leading/trailing blank lines from the empty AI block.
    assert not result.text.startswith("\n")
    assert not result.text.endswith("\n")
