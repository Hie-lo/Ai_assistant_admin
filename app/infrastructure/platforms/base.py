"""Multi-platform adapter core (Phase 6).

The domain core talks to ONE semantic surface (``PlatformClient``) and reads
ONE capability shape (``PlatformCapabilities``) per platform. Adding a
platform means adding one adapter module (transport + capabilities +
platform-specific transforms) and registering it below — the publication
state machine, idempotency, attempts and reconciliation stay shared.

Platform-specific differences live ONLY here:
- Telegram: plain text; captions <= 1024; album <= 10; delete anytime.
- Bale: text is ALWAYS markdown-parsed (the adapter escapes special chars);
  single-media caption <= 4096, album-item caption <= 1024; album <= 10
  (conservative until live certification measures the limit); a message can
  only be deleted within 48h of being sent (deletion failures are tracked,
  never silently lost).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.domain import enums


class PlatformError(Exception):
    """Normalized platform failure carrying a taxonomy error code."""

    def __init__(self, code: enums.PublicationErrorCode, detail: str = "") -> None:
        super().__init__(detail or code.value)
        self.code = code
        self.detail = detail
        #: Seconds to wait before a rate-limited retry (0 otherwise).
        self.retry_after: float = 0.0


class PlatformClient(Protocol):
    """The semantic surface the domain may rely on (mockable per platform).

    ``chat_id``/targets are stored as STRINGS: platform ids can exceed
    32-bit (Bale documents up to 52-bit ids), so no adapter or storage
    layer may assume int32.
    """

    def get_me(self) -> dict: ...
    def get_chat(self, target: str) -> dict: ...
    def get_chat_member(self, target: str, user_id: int) -> dict: ...
    def send_message(self, chat_id: str, text: str) -> int: ...
    def send_photo(self, chat_id: str, url: str, caption: str = "") -> int: ...
    def send_media_group(
        self, chat_id: str, media_urls: list[str], caption: str = ""
    ) -> list[int]: ...
    def edit_message_text(self, chat_id: str, message_id: int, text: str) -> None: ...
    def edit_message_caption(self, chat_id: str, message_id: int, caption: str) -> None: ...
    def delete_message(self, chat_id: str, message_id: int) -> None: ...
    def get_message(self, chat_id: str, message_id: int) -> dict | None: ...


@dataclass(frozen=True)
class PlatformCapabilities:
    """Verified capability/limit data for one platform (adapter spec 3)."""

    platform: str
    send_text: bool = True
    send_single_media: bool = True
    send_media_group: bool = True
    edit_text: bool = True
    edit_caption: bool = True
    delete: bool = True
    inspect_remote: bool = True
    text_max_length: int = 4096
    #: Caption limit for a SINGLE media message.
    single_media_caption_max_length: int = 1024
    #: Caption limit per item of a media group (album).
    album_caption_max_length: int = 1024
    #: Maximum items per album (0 = unsupported).
    media_group_max: int = 0

    def caption_limit_for(self, media_count: int) -> int:
        """The caption/text limit that applies to a rendered payload."""
        if media_count <= 0:
            return self.text_max_length
        if media_count == 1:
            return self.single_media_caption_max_length
        return self.album_caption_max_length


def _build_registry() -> tuple[
    dict[str, PlatformCapabilities], dict[str, object]
]:
    """Capabilities + client factories per supported platform."""
    from app.infrastructure.platforms import bale, telegram

    return (
        {
            telegram.TELEGRAM_CAPABILITIES.platform: telegram.TELEGRAM_CAPABILITIES,
            bale.BALE_CAPABILITIES.platform: bale.BALE_CAPABILITIES,
        },
        {
            enums.Platform.TELEGRAM.value: telegram.build_client,
            enums.Platform.BALE.value: bale.build_client,
        },
    )


_CAPABILITIES: dict[str, PlatformCapabilities] | None = None
_CLIENT_FACTORIES: dict[str, object] | None = None


def _registry() -> tuple[dict[str, PlatformCapabilities], dict[str, object]]:
    """Lazily build (and memoize) the registry.

    Lazy on purpose: the registry imports the adapter modules, which import
    this module — building at import time would cycle. Callers resolve the
    registry on first USE, by which point every module is fully loaded.
    """
    global _CAPABILITIES, _CLIENT_FACTORIES
    if _CAPABILITIES is None or _CLIENT_FACTORIES is None:
        _CAPABILITIES, _CLIENT_FACTORIES = _build_registry()
    return _CAPABILITIES, _CLIENT_FACTORIES


def __getattr__(name: str):
    # PEP 562: SUPPORTED_PLATFORMS stays a module-level name (callers use
    # ``platforms.SUPPORTED_PLATFORMS``) while the registry builds lazily.
    if name == "SUPPORTED_PLATFORMS":
        caps, _ = _registry()
        return frozenset(caps)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

#: Per-platform client overrides (test/dependency-injection seam).
_client_overrides: dict[str, PlatformClient] = {}


def get_capabilities(platform: str) -> PlatformCapabilities:
    caps, _ = _registry()
    try:
        return caps[platform]
    except KeyError:
        raise PlatformError(
            enums.PublicationErrorCode.PLATFORM_UNSUPPORTED_OPERATION,
            f"platform {platform!r} is not supported in V1",
        ) from None


def set_platform_client_override(platform: str, client: PlatformClient | None) -> None:
    """Register (or clear) the client used for ``platform`` in tests/DI."""
    if client is None:
        _client_overrides.pop(platform, None)
    else:
        _client_overrides[platform] = client


def get_platform_client(platform: str) -> PlatformClient:
    """Resolve the shared platform client (settings-backed, override-aware)."""
    if platform in _client_overrides:
        return _client_overrides[platform]
    _, factories = _registry()
    factory = factories.get(platform)
    if factory is None:
        raise PlatformError(
            enums.PublicationErrorCode.PLATFORM_UNSUPPORTED_OPERATION,
            f"platform {platform!r} is not supported in V1",
        )
    return factory()


def remote_text_matches(platform: str, raw_remote_text: str, content_fingerprint: str) -> bool:
    """Whether the raw remote text matches a clean-text fingerprint.

    The remote stores what the bot SENT: on Bale that is the ESCAPED wire
    form, so the escaped form is unescaped before comparing against the
    clean-text fingerprint (prevents false ``remote_modified`` flags on
    content the system itself published).
    """
    import hashlib

    def _sha256(value: str) -> str:
        return hashlib.sha256((value or "").encode("utf-8")).hexdigest()

    raw = raw_remote_text or ""
    if _sha256(raw) == content_fingerprint:
        return True
    if platform == enums.Platform.BALE.value:
        from app.infrastructure.platforms import bale

        # The remote stores what the bot SENT (the escaped wire form);
        # unescape it before comparing against the clean fingerprint.
        return _sha256(bale.unescape_markdown(raw)) == content_fingerprint
    return False
