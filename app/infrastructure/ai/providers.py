"""Concrete AI providers (Phase 4).

- ``TemplateProvider``: deterministic, offline generation from product
  fields. Persian-aware, stable for the same inputs (tests rely on
  determinism), no external calls, no secrets.
- ``OpenAICompatibleProvider``: POST ``{base_url}/chat/completions`` with
  an API key. Never logs the key or the full prompt/response payloads
  (only lengths and the model). Timeouts/5xx/429 -> Transient;
  4xx (auth, bad request) -> Permanent (developer directive rule 14:
  never log secrets or unnecessary sensitive payloads).
"""

from __future__ import annotations

import httpx

from app.infrastructure.ai import (
    AIGenerationRequest,
    AIResult,
    PermanentAIFailure,
    TransientAIFailure,
)


def _clean(value: str) -> str:
    return " ".join((value or "").split())


class TemplateProvider:
    """Deterministic template generation (V1 default).

    Produces stable, readable Persian text from the declared inputs. It is
    intentionally conservative: it only composes facts already present in
    the product (AI is never the source of truth for price/stock/SKU —
    AI spec section 1).
    """

    name = "template"
    model = "template/1.0"

    def generate(self, request: AIGenerationRequest) -> AIResult:
        name = _clean(request.inputs.get("name", ""))
        category = _clean(request.inputs.get("category", ""))
        description = _clean(request.inputs.get("description", ""))
        price = _clean(request.inputs.get("price", ""))
        currency = _clean(request.inputs.get("currency", ""))
        text = self._render(request.definition_key, name, category, description, price, currency)
        if not text:
            raise PermanentAIFailure("template provider produced empty output")
        if len(text) > request.max_length:
            # Deterministic compression: drop the trailing sentence first,
            # then hard-cap (provider-level max, NOT post rendering).
            text = text[: request.max_length].rsplit(" ", 1)[0].rstrip("،. ")
        return AIResult(
            text=text,
            model=self.model,
            provider=self.name,
            prompt_chars=len(request.user_prompt),
            completion_chars=len(text),
        )

    def _render(
        self,
        key: str,
        name: str,
        category: str,
        description: str,
        price: str,
        currency: str,
    ) -> str:
        price_text = f"{price} {currency}".strip()
        if key == "ai_short_title":
            title = name
            if category and category not in name:
                title = f"{name} | {category}"
            return _clean(title)
        if key == "ai_recommendation":
            base = f"پیشنهاد ما: {name}" if name else "پیشنهاد ویژه"
            if category:
                base = f"{base} در دسته‌ی {category}"
            if price_text:
                base = f"{base} با قیمت {price_text}"
            return base
        # ai_description (default)
        parts: list[str] = []
        if name:
            lead = name
            if category and category not in name:
                lead = f"{name} از دسته‌ی {category}"
            parts.append(lead)
        elif category:
            parts.append(f"کالایی از دسته‌ی {category}")
        if description:
            parts.append(description)
        elif parts:
            parts.append("کیفیت و جزئیات این کالا را بررسی کنید.")
        if price_text and parts:
            parts.append(f"قیمت: {price_text}.")
        return _clean(" ".join(parts))


class OpenAICompatibleProvider:
    """Any OpenAI-compatible /chat/completions endpoint."""

    name = "openai_compatible"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 30.0,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        if not base_url or not api_key:
            raise PermanentAIFailure(
                "openai_compatible provider requires base_url and api_key"
            )
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_seconds
        #: Optional provider-specific headers (e.g. OpenRouter attribution
        #: X-Title). Never used for secrets; values are plain metadata.
        self._extra_headers = dict(extra_headers or {})

    def generate(self, request: AIGenerationRequest) -> AIResult:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            **self._extra_headers,
        }
        body = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.user_prompt},
            ],
            "max_tokens": max(request.max_length, 64),
            "temperature": 0.4,
        }
        try:
            response = httpx.post(
                f"{self._base_url}/chat/completions",
                headers=headers,
                json=body,
                timeout=self._timeout,
            )
        except httpx.TimeoutException as exc:
            raise TransientAIFailure("AI provider timed out") from exc
        except httpx.HTTPError as exc:
            err_name = exc.__class__.__name__
            raise TransientAIFailure(f"AI provider network error: {err_name}") from exc

        if response.status_code in (429, 500, 502, 503, 504):
            raise TransientAIFailure(f"AI provider status {response.status_code}")
        if response.status_code >= 400:
            # Never include the body (may echo secrets/requests); code only.
            code = response.status_code
            raise PermanentAIFailure(f"AI provider rejected request: {code}")

        try:
            data = response.json()
            text = data["choices"][0]["message"]["content"]
            model = data.get("model") or self._model
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise TransientAIFailure("AI provider returned malformed payload") from exc
        return AIResult(
            text=text or "",
            model=str(model),
            provider=self.name,
            prompt_chars=len(request.user_prompt),
            completion_chars=len(text or ""),
        )
