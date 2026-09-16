"""Unit tests for the Phase 4 AI policy + template provider."""

from __future__ import annotations

import pytest
from app.domain import ai_policy
from app.domain.ai_policy import (
    ArtifactRecord,
    auto_generation_eligible,
    dependency_fingerprint,
    reuse_eligible,
    should_refund,
    should_retry,
    validate_generated_text,
)
from app.infrastructure.ai import (
    AIGenerationRequest,
    PermanentAIFailure,
)
from app.infrastructure.ai.providers import (
    OpenAICompatibleProvider,
    TemplateProvider,
)

pytestmark = pytest.mark.unit


def test_fingerprint_is_stable_and_order_insensitive_for_inputs() -> None:
    a = dependency_fingerprint("ai_description", 1, {"name": "X", "price": "1"})
    b = dependency_fingerprint("ai_description", 1, {"price": "1", "name": "X"})
    c = dependency_fingerprint("ai_description", 2, {"name": "X", "price": "1"})
    d = dependency_fingerprint("ai_description", 1, {"name": "Y", "price": "1"})
    assert a == b
    assert a != c  # different definition version
    assert a != d  # different inputs


def test_reuse_eligible_requires_approved_same_version_same_fingerprint() -> None:
    fp = dependency_fingerprint("k", 1, {"name": "X"})
    good = ArtifactRecord("APPROVED", 1, fp)
    wrong_version = ArtifactRecord("APPROVED", 2, fp)
    wrong_fp = ArtifactRecord("APPROVED", 1, "deadbeef")
    pending = ArtifactRecord("PENDING_APPROVAL", 1, fp)
    assert reuse_eligible(good, definition_version=1, fingerprint=fp) is True
    assert reuse_eligible(wrong_version, definition_version=1, fingerprint=fp) is False
    assert reuse_eligible(wrong_fp, definition_version=1, fingerprint=fp) is False
    assert reuse_eligible(pending, definition_version=1, fingerprint=fp) is False
    assert reuse_eligible(None, definition_version=1, fingerprint=fp) is False


def test_retry_is_bounded_and_transient_only() -> None:
    assert should_retry("TRANSIENT", 0) is True
    assert should_retry("TRANSIENT", 1) is True
    assert should_retry("TRANSIENT", 2) is True
    assert should_retry("TRANSIENT", ai_policy.MAX_ATTEMPTS) is False
    assert should_retry("PERMANENT", 0) is False
    assert should_retry("INVALID_OUTPUT", 0) is True  # bounded like transient


def test_refund_policy() -> None:
    assert should_refund(artifact_created=False) is True
    assert should_refund(artifact_created=True) is False


def test_auto_eligibility_matrix() -> None:
    base = dict(
        plan_ai_available=True,
        credits_available=5,
        business_automatic_enabled=True,
        has_valid_approved=False,
        has_pending_or_rejected_only=True,
    )
    assert auto_generation_eligible(**base).eligible is True
    assert (
        auto_generation_eligible(**{**base, "business_automatic_enabled": False}).eligible
        is False
    )
    assert (
        auto_generation_eligible(**{**base, "plan_ai_available": False}).eligible is False
    )
    assert (
        auto_generation_eligible(**{**base, "has_valid_approved": True}).eligible is False
    )
    assert (
        auto_generation_eligible(**{**base, "credits_available": 0}).eligible is False
    )
    assert (
        auto_generation_eligible(**{**base, "has_pending_or_rejected_only": False}).eligible
        is False
    )


def test_validate_generated_text_rules() -> None:
    assert validate_generated_text("hello", max_length=10) is None
    assert validate_generated_text("", max_length=10) == "empty output"
    assert validate_generated_text("   ", max_length=10) == "empty output"
    assert validate_generated_text("x" * 20, max_length=10) is not None
    assert validate_generated_text("a\x00b", max_length=10) is not None
    assert validate_generated_text("ok\ntab\there", max_length=100) is None


def _req(key: str = "ai_description", **inputs) -> AIGenerationRequest:
    return AIGenerationRequest(
        definition_key=key,
        inputs={"name": inputs.get("name", "Laptop X"), **inputs},
        system_prompt="sys",
        user_prompt="user prompt",
        max_length=inputs.pop("max_length", 800),
    )


def test_template_provider_deterministic_and_factual() -> None:
    provider = TemplateProvider()
    req = _req(
        name="Laptop X",
        category="electronics",
        description="A fast laptop.",
        price="1000",
        currency="IRT",
    )
    first = provider.generate(req)
    second = provider.generate(req)
    assert first.text == second.text  # deterministic
    assert "Laptop X" in first.text
    assert "electronics" in first.text
    assert first.provider == "template"
    assert first.model.startswith("template")
    assert first.completion_chars == len(first.text)


def test_template_provider_short_title_uses_only_declared_fields() -> None:
    provider = TemplateProvider()
    req = _req(key="ai_short_title", name="Laptop X", category="electronics")
    result = provider.generate(req)
    assert "Laptop X" in result.text
    assert "electronics" in result.text


def test_template_provider_respects_max_length() -> None:
    provider = TemplateProvider()
    long_name = "word " * 500
    req = _req(name=long_name.strip(), max_length=100)
    result = provider.generate(req)
    assert len(result.text) <= 100


def test_template_provider_empty_name_still_produces_output() -> None:
    provider = TemplateProvider()
    req = _req(key="ai_description", name="", category="toys")
    result = provider.generate(req)
    assert result.text.strip() != ""
    assert "toys" in result.text


def test_openai_compatible_requires_credentials() -> None:
    with pytest.raises(PermanentAIFailure):
        OpenAICompatibleProvider(base_url="", api_key="", model="m")
