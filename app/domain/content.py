"""Content composition & rendering (Phase 4 domain, pure).

The renderer is the single deterministic path for preview today and for
publication later (Post & Publication spec sections 7 and 23: preview must
use the same rendering stack as closely as practical).

Security model (SECURITY_THREAT_MODEL section 10):
- The token grammar is a strict allowlist; nothing is ever ``eval``-ed.
- Tokens are parsed ONCE against preset template text. Resolved values
  (product fields, custom attributes, AI text) are inert data: they are
  substituted in a second pass and NEVER re-scanned for tokens, so product
  fields or AI output containing ``{name}``-like text cannot grant access
  to other fields or escalate.
- Unknown AI/product tokens are rejected when a preset version is saved
  (typo protection); at render time, a missing *value* degrades to an
  explicit warning + omission, never a crash.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from app.domain.enums import BlockOwnership, BlockType

#: Allowlisted token grammar: {product_field}, {attr.<key>}, {ai_<key>}
#: (AI output tokens are the registered definition key, AI spec section 14).
TOKEN_RE = re.compile(r"\{([a-z0-9_]+)(?:\.([a-z0-9_]+))?\}")

#: Product-field tokens available to every preset.
PRODUCT_FIELD_TOKENS: frozenset[str] = frozenset(
    {
        "name",
        "price",
        "currency",
        "stock",
        "category",
        "sku",
        "barcode",
        "description",
        "hashtags",
    }
)

#: Content priorities (Post & Publication spec section 8). Lower number =
#: more essential; trimming removes the least essential first.
PRIORITY_IDENTITY = 1  # product identity (name)
PRIORITY_PRICE_STOCK = 2  # price / availability
PRIORITY_CONTACT = 3  # required contact / CTA
PRIORITY_ATTRIBUTES = 4  # essential product attributes / custom fields
PRIORITY_AI = 5  # approved AI content
PRIORITY_HASHTAGS = 6  # hashtags
PRIORITY_DECORATIVE = 7  # static / decorative content

DEFAULT_MAX_MESSAGE_LENGTH = 4096  # Telegram sendMessage limit; conservative floor


class ContentError(ValueError):
    """Preset/block validation failure (raised at save/activate time)."""


@dataclass(frozen=True)
class ContentBlock:
    """One structured content block (spec section 4)."""

    block_id: str
    type: BlockType
    ownership: BlockOwnership
    payload: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ContentBlock:
        block_id = str(raw.get("id") or "")
        if not block_id or len(block_id) > 64:
            raise ContentError("block id (1-64 chars) is required")
        try:
            btype = BlockType(raw.get("type"))
        except ValueError as exc:
            raise ContentError(f"unknown block type: {raw.get('type')!r}") from exc
        try:
            ownership = BlockOwnership(raw.get("ownership") or "STATIC")
        except ValueError as exc:
            raise ContentError(
                f"unknown block ownership: {raw.get('ownership')!r}"
            ) from exc
        payload = raw.get("payload") or {}
        if not isinstance(payload, dict):
            raise ContentError(f"block {block_id}: payload must be an object")
        return cls(block_id=block_id, type=btype, ownership=ownership, payload=payload)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.block_id,
            "type": self.type.value,
            "ownership": self.ownership.value,
            "payload": dict(self.payload),
        }

    def tokens_in_template(self) -> list[str]:
        """Tokens present in this block's template text (if any)."""
        if self.type is BlockType.STATIC_TEXT:
            return TOKEN_RE.findall(str(self.payload.get("text") or ""))
        if self.type is BlockType.CONTACT:
            return TOKEN_RE.findall(str(self.payload.get("text") or ""))
        if self.type is BlockType.SEPARATOR:
            return TOKEN_RE.findall(str(self.payload.get("text") or ""))
        return []


def _validate_block_tokens(block: ContentBlock, ai_keys: frozenset[str]) -> None:
    """Reject unknown tokens at save time (allowlist enforcement).

    Token grammar (AI spec section 14): product fields ({name}, {price},
    ...), custom fields ({attr.<key>}) and AI outputs ({ai_description},
    {ai_short_title}, ... — the registered definition key as-is).
    """
    for match in TOKEN_RE.finditer(
        " ".join(
            str(block.payload.get(k) or "")
            for k in ("text",)
            if block.payload.get(k) is not None
        )
    ):
        base, sub = match.group(1), match.group(2)
        if base == "attr":
            if not sub:
                raise ContentError(f"block {block.block_id}: {{attr}} needs a key")
            continue  # custom-field keys vary per mapping; validated at render
        if base.startswith("ai_"):
            if base not in ai_keys:
                raise ContentError(
                    f"block {block.block_id}: unknown AI output key {{{base}}}"
                )
            continue
        if base not in PRODUCT_FIELD_TOKENS:
            raise ContentError(f"block {block.block_id}: unknown token {{{base}}}")


def validate_blocks(
    blocks: list[dict[str, Any]], ai_keys: frozenset[str]
) -> list[ContentBlock]:
    """Parse + validate a preset's block list (raises ContentError)."""
    if not blocks or len(blocks) > 64:
        raise ContentError("a preset needs 1-64 blocks")
    parsed = [ContentBlock.from_dict(b) for b in blocks]
    ids = [b.block_id for b in parsed]
    if len(ids) != len(set(ids)):
        raise ContentError("block ids must be unique within a preset")
    for block in parsed:
        _validate_block_tokens(block, ai_keys)
    return parsed


def block_priority(block: ContentBlock) -> int:
    """Essentiality priority used by length handling (spec section 8)."""
    if block.type is BlockType.PRODUCT_FIELD:
        field_name = str(block.payload.get("field") or "")
        if field_name == "name":
            return PRIORITY_IDENTITY
        if field_name in {"price", "stock"}:
            return PRIORITY_PRICE_STOCK
        return PRIORITY_ATTRIBUTES
    if block.type is BlockType.CONTACT:
        return PRIORITY_CONTACT
    if block.type is BlockType.AI_OUTPUT:
        return PRIORITY_AI
    if block.type is BlockType.HASHTAG_SET:
        return PRIORITY_HASHTAGS
    return PRIORITY_DECORATIVE


@dataclass
class RenderContext:
    """Everything the renderer may resolve (all values are inert data)."""

    product_fields: dict[str, str]  # name, price, currency, stock, ...
    attributes: dict[str, Any]  # custom fields
    ai_outputs: dict[str, str]  # APPROVED artifact text only
    media_urls: list[str] = field(default_factory=list)
    max_length: int = DEFAULT_MAX_MESSAGE_LENGTH


@dataclass
class RenderedBlock:
    block_id: str
    type: BlockType
    ownership: BlockOwnership
    priority: int
    text: str
    kept: bool
    warnings: list[str] = field(default_factory=list)


@dataclass
class RenderResult:
    text: str
    blocks: list[RenderedBlock]
    total_chars: int
    max_length: int
    fits: bool
    blocked_reason: str | None
    warnings: list[str]


def _resolve_token(
    token: str,
    sub: str | None,
    ctx: RenderContext,
    warnings: list[str],
    block_id: str,
) -> str:
    if token == "attr":
        value = ctx.attributes.get(sub or "")
        if value is None:
            warnings.append(f"{block_id}: missing custom field '{sub}'")
            return ""
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return str(value)
    if token.startswith("ai_"):
        value = ctx.ai_outputs.get(token)
        if value is None:
            warnings.append(f"{block_id}: missing approved AI output '{token}'")
            return ""
        return value
    value = ctx.product_fields.get(token)
    if value is None or value == "":
        warnings.append(f"{block_id}: missing product field '{token}'")
        return ""
    return value


def _expand(text: str, ctx: RenderContext, warnings: list[str], block_id: str) -> str:
    """Second pass: substitute resolved values; never re-parse them."""
    return TOKEN_RE.sub(
        lambda m: _resolve_token(m.group(1), m.group(2), ctx, warnings, block_id),
        text,
    )


def _render_block(block: ContentBlock, ctx: RenderContext) -> RenderedBlock:
    warnings: list[str] = []
    text = ""
    p = block.payload
    if block.type is BlockType.STATIC_TEXT:
        text = _expand(str(p.get("text") or ""), ctx, warnings, block.block_id).strip()
    elif block.type is BlockType.SEPARATOR:
        text = str(p.get("text") or "---").strip()
    elif block.type is BlockType.CONTACT:
        text = _expand(str(p.get("text") or ""), ctx, warnings, block.block_id).strip()
    elif block.type is BlockType.PRODUCT_FIELD:
        field_name = str(p.get("field") or "")
        text = (ctx.product_fields.get(field_name) or "").strip()
        if not text:
            warnings.append(f"{block.block_id}: missing product field '{field_name}'")
    elif block.type is BlockType.CUSTOM_FIELD:
        key = str(p.get("key") or "")
        value = ctx.attributes.get(key)
        if value is None:
            warnings.append(f"{block.block_id}: missing custom field '{key}'")
            text = ""
        elif isinstance(value, (dict, list)):
            text = json.dumps(value, ensure_ascii=False)
        else:
            text = str(value).strip()
        display = str(p.get("display_name") or "").strip()
        if text and display:
            text = f"{display}: {text}"
    elif block.type is BlockType.AI_OUTPUT:
        key = str(p.get("key") or "")
        text = (ctx.ai_outputs.get(key) or "").strip()
        if not text:
            warnings.append(
                f"{block.block_id}: no approved AI output '{key}' (block omitted)"
            )
    elif block.type is BlockType.HASHTAG_SET:
        text = (ctx.product_fields.get("hashtags") or "").strip()
    elif block.type is BlockType.DATE:
        created = ctx.product_fields.get("created_at") or ""
        text = created.strip()
    elif block.type is BlockType.MEDIA_REFERENCE:
        text = f"[media: {len(ctx.media_urls)} item(s)]" if ctx.media_urls else ""
        if not text:
            warnings.append(f"{block.block_id}: product has no media")
    return RenderedBlock(
        block_id=block.block_id,
        type=block.type,
        ownership=block.ownership,
        priority=block_priority(block),
        text=text,
        kept=True,
        warnings=list(warnings),
    )


def render_blocks(
    blocks: list[ContentBlock],
    ctx: RenderContext,
) -> RenderResult:
    """Render blocks to final text with length handling (spec section 8).

    If the full composition exceeds ``ctx.max_length``, blocks are dropped
    from the LEAST essential priority upward (decorative before hashtags
    before AI before attributes; identity/price/contact are never dropped
    silently). If it still does not fit with only the three most essential
    priorities kept, the render is BLOCKED with a clear reason (spec:
    otherwise block publication with a clear reason — V1: no splitting).
    """
    rendered = [_render_block(b, ctx) for b in blocks]
    all_warnings = [w for b in rendered for w in b.warnings]

    def _assemble(kept: list[RenderedBlock]) -> str:
        return "\n".join(b.text for b in kept if b.text)

    total = len(_assemble(rendered))
    if total <= ctx.max_length:
        return RenderResult(
            text=_assemble(rendered),
            blocks=rendered,
            total_chars=total,
            max_length=ctx.max_length,
            fits=True,
            blocked_reason=None,
            warnings=all_warnings,
        )

    # Drop from least essential (highest priority number) downward.
    kept = list(rendered)
    for threshold in (PRIORITY_DECORATIVE, PRIORITY_HASHTAGS, PRIORITY_AI,
                      PRIORITY_ATTRIBUTES):
        kept = [b for b in kept if b.priority < threshold]
        kept_ids = {id(b) for b in kept}
        for b in rendered:
            if id(b) not in kept_ids:
                b.kept = False
        total = len(_assemble(kept))
        if total <= ctx.max_length:
            return RenderResult(
                text=_assemble(kept),
                blocks=rendered,
                total_chars=total,
                max_length=ctx.max_length,
                fits=True,
                blocked_reason=None,
                warnings=[*all_warnings, "length limit: lower-priority blocks omitted"],
            )

    total = len(_assemble(kept))
    reason = (
        f"content exceeds the {ctx.max_length}-character limit even after "
        "removing all optional blocks; shorten the product fields"
    )
    return RenderResult(
        text=_assemble(kept),
        blocks=rendered,
        total_chars=total,
        max_length=ctx.max_length,
        fits=False,
        blocked_reason=reason,
        warnings=[*all_warnings, reason],
    )
