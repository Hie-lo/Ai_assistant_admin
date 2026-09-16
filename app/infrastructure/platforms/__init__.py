"""Platform adapters (Phase 5/6).

Public API:
- base: multi-platform core (PlatformError, capabilities, client registry,
  remote-text matching).
- telegram: Phase 5 adapter (shared organization bot).
- bale: Phase 6 adapter (shared organization bot; 48h delete limit, always
  markdown -> escaped text, no message lookup -> inspection unsupported).

Resolution by platform name goes through base.get_platform_client /
base.get_capabilities — services must never import a concrete adapter for
a hardcoded platform (that is how Bale became second-class in Phase 5).
"""

from app.infrastructure.platforms import bale, base, telegram

__all__ = ["base", "bale", "telegram"]
