"""AI provider abstraction (Phase 4 infrastructure).

Two providers behind one contract (owner decision 2026-09-13):
- ``template``: deterministic, offline, no keys — default in V1;
- ``openai_compatible``: any OpenAI-compatible /chat/completions endpoint
  (base URL + API key + model from settings/env; key never logged).

The provider sees a structured ``AIGenerationRequest`` (resolved inputs +
rendered prompts) and returns an ``AIResult``. Application layer owns
credits, retries, validation and persistence — the provider is a stateless
transport/generator.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class TransientAIFailure(Exception):
    """Network/5xx/429 — retryable within the bounded budget."""


class PermanentAIFailure(Exception):
    """Auth/4xx/unsupported — not retryable; manual fallback."""


@dataclass(frozen=True)
class AIGenerationRequest:
    definition_key: str
    inputs: dict[str, str]  # resolved, declared input fields
    system_prompt: str
    user_prompt: str
    max_length: int


@dataclass(frozen=True)
class AIResult:
    text: str
    model: str
    provider: str
    prompt_chars: int
    completion_chars: int


class AIProvider(Protocol):
    name: str

    def generate(self, request: AIGenerationRequest) -> AIResult: ...


def get_ai_provider() -> AIProvider:
    """Factory: resolve the configured provider (cached per settings)."""
    from app.config.settings import get_settings

    settings = get_settings()
    kind = (settings.ai_provider or "template").strip().lower()
    if kind == "openai_compatible":
        from app.infrastructure.ai.providers import OpenAICompatibleProvider

        return OpenAICompatibleProvider(
            base_url=settings.ai_openai_base_url,
            api_key=settings.ai_openai_api_key,
            model=settings.ai_openai_model,
            timeout_seconds=settings.ai_request_timeout_seconds,
        )
    from app.infrastructure.ai.providers import TemplateProvider

    return TemplateProvider()


# Test/dependency-injection override point (never reads network in tests).
_provider_override: AIProvider | None = None


def set_ai_provider_override(provider: AIProvider | None) -> None:
    global _provider_override
    _provider_override = provider


def get_ai_provider_or_override() -> AIProvider:
    if _provider_override is not None:
        return _provider_override
    return get_ai_provider()
