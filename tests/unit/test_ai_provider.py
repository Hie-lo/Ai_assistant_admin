"""AI provider unit tests: OpenAI-compatible transport + OpenRouter wiring.

The HTTP layer is faked (monkeypatched httpx.post) so no network is touched;
assertions cover the exact URL/headers/model contract, the OpenRouter
attribution header, and the failure classification (transient vs permanent).
"""

from __future__ import annotations

import httpx
import pytest
from app.config.settings import get_settings
from app.infrastructure.ai import (
    AIGenerationRequest,
    PermanentAIFailure,
    TransientAIFailure,
    get_ai_provider,
)
from app.infrastructure.ai.providers import OpenAICompatibleProvider

REQUEST = AIGenerationRequest(
    definition_key="ai_description",
    inputs={"name": "Laptop X"},
    system_prompt="sys",
    user_prompt="user prompt",
    max_length=500,
)


class _FakeResponse:
    def __init__(self, status_code: int, json_body: object | None = None) -> None:
        self.status_code = status_code
        self._json = json_body or {}

    def json(self) -> object:
        return self._json


def _ok_json(model: str = "openai/gpt-4o-mini") -> dict:
    return {
        "model": model,
        "choices": [{"message": {"content": "متن تولیدشده"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


@pytest.fixture()
def captured(monkeypatch: pytest.MonkeyPatch):
    """Monkeypatch httpx.post; record the last call; return scripted reply."""
    calls: list[dict] = []
    state: dict = {"response": _FakeResponse(200, _ok_json())}

    def fake_post(url: str, **kwargs) -> _FakeResponse:
        calls.append({"url": url, **kwargs})
        if isinstance(state["response"], Exception):
            raise state["response"]
        return state["response"]

    monkeypatch.setattr(httpx, "post", fake_post)
    return {"calls": calls, "state": state}


def _make_provider(**kw) -> OpenAICompatibleProvider:
    base = dict(
        base_url="https://openrouter.ai/api/v1",
        api_key="sk-or-test",
        model="openai/gpt-4o-mini",
    )
    base.update(kw)
    return OpenAICompatibleProvider(**base)


# --- successful generation contract ------------------------------------------


def test_success_posts_to_chat_completions_with_bearer_key(captured):
    provider = _make_provider()
    result = provider.generate(REQUEST)

    call = captured["calls"][0]
    assert call["url"] == "https://openrouter.ai/api/v1/chat/completions"
    headers = call["headers"]
    assert headers["Authorization"] == "Bearer sk-or-test"
    body = call["json"]
    assert body["model"] == "openai/gpt-4o-mini"
    assert body["messages"][0] == {"role": "system", "content": "sys"}
    assert body["messages"][1] == {"role": "user", "content": "user prompt"}
    assert body["max_tokens"] == 500

    assert result.text == "متن تولیدشده"
    assert result.model == "openai/gpt-4o-mini"
    assert result.provider == "openai_compatible"
    assert result.completion_chars == len("متن تولیدشده")


def test_trailing_slash_in_base_url_is_normalized(captured):
    provider = _make_provider(base_url="https://openrouter.ai/api/v1/")
    provider.generate(REQUEST)
    assert captured["calls"][0]["url"] == "https://openrouter.ai/api/v1/chat/completions"


def test_extra_headers_are_merged_into_request(captured):
    provider = _make_provider(extra_headers={"X-Title": "ai-assistant-admin"})
    provider.generate(REQUEST)
    headers = captured["calls"][0]["headers"]
    assert headers["X-Title"] == "ai-assistant-admin"
    assert headers["Authorization"] == "Bearer sk-or-test"


def test_missing_base_url_or_key_is_permanent():
    with pytest.raises(PermanentAIFailure):
        OpenAICompatibleProvider(base_url="", api_key="k", model="m")
    with pytest.raises(PermanentAIFailure):
        OpenAICompatibleProvider(base_url="https://x", api_key="", model="m")


# --- failure classification ---------------------------------------------------


def test_429_and_5xx_are_transient(captured):
    provider = _make_provider()
    for status in (429, 500, 502, 503, 504):
        captured["state"]["response"] = _FakeResponse(status)
        with pytest.raises(TransientAIFailure):
            provider.generate(REQUEST)


def test_auth_billing_and_bad_request_are_permanent(captured):
    provider = _make_provider()
    for status in (400, 401, 402, 403, 404):
        captured["state"]["response"] = _FakeResponse(status)
        with pytest.raises(PermanentAIFailure):
            provider.generate(REQUEST)
    # 402 is OpenRouter's "insufficient credits" — correctly not retryable.


def test_timeout_and_network_errors_are_transient(captured):
    provider = _make_provider()
    captured["state"]["response"] = httpx.TimeoutException("slow")
    with pytest.raises(TransientAIFailure):
        provider.generate(REQUEST)
    captured["state"]["response"] = httpx.ConnectError("refused")
    with pytest.raises(TransientAIFailure):
        provider.generate(REQUEST)


def test_malformed_payload_is_transient(captured):
    provider = _make_provider()
    for bad in (None, {"choices": []}, {"choices": [{"message": {}}]}):
        captured["state"]["response"] = _FakeResponse(200, bad)
        with pytest.raises(TransientAIFailure):
            provider.generate(REQUEST)


def test_error_bodies_are_never_included_in_messages(captured):
    provider = _make_provider()
    captured["state"]["response"] = _FakeResponse(401, {"error": "key leaked here"})
    with pytest.raises(PermanentAIFailure) as excinfo:
        provider.generate(REQUEST)
    assert "leaked" not in str(excinfo.value)


# --- get_ai_provider wiring ----------------------------------------------------


def _env(monkeypatch: pytest.MonkeyPatch, **vars) -> None:
    for key, value in vars.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


def test_get_ai_provider_defaults_to_template(monkeypatch):
    _env(monkeypatch, AI_PROVIDER="template")
    assert get_ai_provider().name == "template"
    get_settings.cache_clear()


def test_get_ai_provider_openrouter_uses_openrouter_settings(monkeypatch):
    _env(
        monkeypatch,
        AI_PROVIDER="openrouter",
        AI_OPENROUTER_API_KEY="sk-or-test-123",
        AI_OPENROUTER_MODEL="anthropic/claude-3.5-sonnet",
    )
    provider = get_ai_provider()
    get_settings.cache_clear()
    assert provider.name == "openai_compatible"
    # The OpenRouter base URL default is wired in...
    assert provider._base_url == "https://openrouter.ai/api/v1"
    assert provider._model == "anthropic/claude-3.5-sonnet"
    # ...and OpenRouter's attribution header is attached (app name only).
    assert provider._extra_headers.get("X-Title") == "ai-assistant-admin"


def test_get_ai_provider_openrouter_missing_key_fails_permanently(monkeypatch):
    _env(monkeypatch, AI_PROVIDER="openrouter", AI_OPENROUTER_API_KEY="")
    get_settings.cache_clear()
    with pytest.raises(PermanentAIFailure) as excinfo:
        get_ai_provider()
    get_settings.cache_clear()
    assert "base_url and api_key" in str(excinfo.value)


def test_get_ai_provider_openai_compatible_unchanged(monkeypatch):
    _env(
        monkeypatch,
        AI_PROVIDER="openai_compatible",
        AI_OPENAI_BASE_URL="https://llm.local/v1",
        AI_OPENAI_API_KEY="k-1",
        AI_OPENAI_MODEL="local-model",
    )
    provider = get_ai_provider()
    get_settings.cache_clear()
    assert provider._base_url == "https://llm.local/v1"
    assert provider._model == "local-model"
    assert provider._extra_headers == {}
