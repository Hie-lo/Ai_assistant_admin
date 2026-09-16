"""AI generation service (Phase 4 application layer).

Flow (manual V1; owner-approved):
1. permission + tenant scope (route) -> active subscription + plan AI
   entitlement -> active definition for the key;
2. REUSE: an APPROVED artifact for the same (product, key, definition
   version, declared inputs) is returned as-is — no charge (AI spec 6);
3. consume credits atomically (idempotent) BEFORE the provider call;
4. bounded provider attempts (3) for transient failures; permanent
   failures stop immediately;
5. validate output (length/shape/safety); invalid output consumes an
   attempt like a transient failure (AI spec 11);
6. store a PENDING_APPROVAL artifact (approval is explicit — owner
   decision) or, on total failure, refund the credits (no usable
   artifact) and report a manual fallback.

Everything happens in ONE transaction (request-scoped session): a crash
rolls back both the charge and the artifact — no orphaned deductions.
AI never writes product facts (directive rule 12).
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application import ai_definitions, credits
from app.application import entitlements as entsvc
from app.application.audit import AuditService
from app.domain import ai_policy, enums
from app.domain.errors import ConflictError, ValidationError
from app.infrastructure.ai import (
    AIGenerationRequest,
    PermanentAIFailure,
    TransientAIFailure,
    get_ai_provider_or_override,
)
from app.infrastructure.db.models import (
    AIOutputArtifact,
    AIOutputDefinition,
    Business,
    Product,
    User,
)

#: Default system instructions (provider-agnostic; output stays inert).
SYSTEM_PROMPT = (
    "You are a professional Persian business copywriter. "
    "Write factual, natural Persian text strictly from the provided product data. "
    "Never invent prices, stock, SKUs or technical facts. "
    "Return ONLY the output text, no markdown, no commentary."
)

# Fast default backoff base (seconds); tests patch this to 0.
_BACKOFF_BASE = 0.25
_MAX_BACKOFF = 5.0


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _get_product(db: Session, *, business: Business, product_id: uuid.UUID) -> Product:
    product = db.get(Product, product_id)
    if product is None or product.business_id != business.business_id:
        from app.domain.errors import NotFoundError

        raise NotFoundError("product not found")
    return product


def resolve_inputs(product: Product, definition: AIOutputDefinition) -> dict[str, str]:
    """Resolve the definition's declared input fields from the product."""
    fields = {
        "name": product.name or "",
        "price": str(product.price) if product.price is not None else "",
        "currency": product.currency or "IRT",
        "stock": str(product.stock) if product.stock is not None else "",
        "category": product.category or "",
        "sku": product.sku or "",
        "barcode": product.barcode or "",
        "description": product.description or "",
        "hashtags": str((product.attributes or {}).get("hashtags") or ""),
        "created_at": (product.created_at or _utcnow()).isoformat()[:10],
    }
    out: dict[str, str] = {}
    for f in definition.input_fields or []:
        if f.startswith("attributes."):
            out[f] = str((product.attributes or {}).get(f.split(".", 1)[1]) or "")
        else:
            out[f] = fields.get(f, "")
    return out


def _render_prompt(definition: AIOutputDefinition, inputs: dict[str, str]) -> str:
    """Fill the prompt template with inputs (inert substitution, no re-parse)."""
    from app.domain.content import TOKEN_RE

    return TOKEN_RE.sub(
        lambda m: inputs.get(m.group(1) or "", ""), definition.prompt_template
    ).strip()


def list_artifacts(
    db: Session, *, business: Business, product_id: uuid.UUID
) -> list[AIOutputArtifact]:
    _get_product(db, business=business, product_id=product_id)
    return list(
        db.scalars(
            select(AIOutputArtifact)
            .where(
                AIOutputArtifact.business_id == business.business_id,
                AIOutputArtifact.product_id == product_id,
            )
            .order_by(AIOutputArtifact.created_at.desc())
        ).all()
    )


def get_artifact(
    db: Session, *, business: Business, artifact_id: uuid.UUID
) -> AIOutputArtifact:
    artifact = db.get(AIOutputArtifact, artifact_id)
    if artifact is None or artifact.business_id != business.business_id:
        from app.domain.errors import NotFoundError

        raise NotFoundError("AI artifact not found")
    return artifact


def _find_reusable(
    db: Session,
    *,
    product_id: uuid.UUID,
    key: str,
    definition_version: int,
    fingerprint: str,
) -> AIOutputArtifact | None:
    rows = db.scalars(
        select(AIOutputArtifact).where(
            AIOutputArtifact.product_id == product_id,
            AIOutputArtifact.output_definition_key == key,
            AIOutputArtifact.output_definition_version == definition_version,
            AIOutputArtifact.source_dependency_fingerprint == fingerprint,
            AIOutputArtifact.status == enums.AIArtifactStatus.APPROVED.value,
        )
    ).all()
    return max(rows, key=lambda a: a.created_at) if rows else None


def _sleep_backoff(attempt: int) -> None:
    delay = min(_BACKOFF_BASE * (2 ** (attempt - 1)), _MAX_BACKOFF)
    if delay > 0:
        time.sleep(delay)


def generate(
    db: Session,
    *,
    business: Business,
    actor: User,
    product_id: uuid.UUID,
    definition_key: str,
    is_retry: bool = False,
) -> dict:
    """One manual generation (or bounded retry) for product + key."""
    product = _get_product(db, business=business, product_id=product_id)
    entsvc.require_ai_available(db, business_id=business.business_id)
    definition = ai_definitions.get_active_definition(db, definition_key)

    inputs = resolve_inputs(product, definition)
    fingerprint = ai_policy.dependency_fingerprint(
        definition.key, definition.version, inputs
    )
    reusable = _find_reusable(
        db,
        product_id=product.product_id,
        key=definition.key,
        definition_version=definition.version,
        fingerprint=fingerprint,
    )
    if ai_policy.reuse_eligible(
        ai_policy.ArtifactRecord(
            status=reusable.status,
            definition_version=reusable.output_definition_version,
            dependency_fingerprint=reusable.source_dependency_fingerprint,
        )
        if reusable
        else None,
        definition_version=definition.version,
        fingerprint=fingerprint,
    ):
        AuditService(db).record(
            action="ai.reused",
            actor_user_id=actor.user_id,
            business_id=business.business_id,
            target_type="ai_artifact",
            target_id=str(reusable.artifact_id),
            meta={"definition_key": definition.key, "version": definition.version},
        )
        return {
            "artifact": reusable,
            "reused": True,
            "definition": definition,
        }

    # --- Charge BEFORE the external call (atomic; one transaction). ---
    charge_key = f"ai-gen:{product.product_id}:{definition.key}:{uuid.uuid4().hex[:12]}"
    credits.consume_credit(
        db,
        business_id=business.business_id,
        amount=definition.cost_credits,
        idempotency_key=charge_key,
        reference=f"ai_generation:{definition.key}",
        actor_user_id=actor.user_id,
    )

    request = AIGenerationRequest(
        definition_key=definition.key,
        inputs=inputs,
        system_prompt=SYSTEM_PROMPT,
        user_prompt=_render_prompt(definition, inputs),
        max_length=definition.max_output_length,
    )
    provider = get_ai_provider_or_override()

    attempts = 0
    failure_kind = "PERMANENT"
    failure_message = ""
    valid_result = None
    while True:
        attempts += 1
        try:
            candidate = provider.generate(request)
            invalid = ai_policy.validate_generated_text(
                candidate.text, max_length=definition.max_output_length
            )
            if invalid is None:
                valid_result = candidate
                break
            # Invalid output: keep the retry budget, but the candidate is
            # NOT usable (it must never be stored).
            failure_kind = "INVALID_OUTPUT"
            failure_message = invalid
        except PermanentAIFailure as exc:
            failure_kind = "PERMANENT"
            failure_message = str(exc)
            break  # not retryable
        except TransientAIFailure as exc:
            failure_kind = "TRANSIENT"
            failure_message = str(exc)
        if not ai_policy.should_retry(failure_kind, attempts):
            break
        _sleep_backoff(attempts)

    if valid_result is None:
        # No usable artifact -> refund (policy: failed infra calls
        # refundable; nothing stored to reject).
        credits.refund_credit(
            db,
            business_id=business.business_id,
            amount=definition.cost_credits,
            idempotency_key=f"refund:{charge_key}",
            reference=charge_key,
            actor_user_id=actor.user_id,
        )
        AuditService(db).record(
            action="ai_generation.failed",
            outcome=enums.AuditOutcome.FAILURE,
            actor_user_id=actor.user_id,
            business_id=business.business_id,
            target_type="product",
            target_id=str(product.product_id),
            meta={
                "definition_key": definition.key,
                "failure_kind": failure_kind,
                "attempts": attempts,
                "reason": failure_message[:300],
            },
        )
        raise ValidationError(
            f"AI generation failed ({failure_kind.lower()} after {attempts} "
            f"attempt(s)): {failure_message} — manual fallback: write the "
            "content yourself or retry later"
        )

    artifact = AIOutputArtifact(
        product_id=product.product_id,
        business_id=business.business_id,
        output_definition_id=definition.definition_id,
        output_definition_key=definition.key,
        output_definition_version=definition.version,
        source_dependency_fingerprint=fingerprint,
        generated_value=valid_result.text.strip(),
        status=enums.AIArtifactStatus.PENDING_APPROVAL.value,
        generated_at=_utcnow(),
        model=valid_result.model,
        provider=valid_result.provider,
        prompt_chars=valid_result.prompt_chars,
        completion_chars=valid_result.completion_chars,
    )
    db.add(artifact)
    db.flush()
    AuditService(db).record(
        action="ai.generated" if not is_retry else "ai.retried",
        actor_user_id=actor.user_id,
        business_id=business.business_id,
        target_type="ai_artifact",
        target_id=str(artifact.artifact_id),
        meta={
            "definition_key": definition.key,
            "version": definition.version,
            "attempts": attempts,
        },
    )
    return {"artifact": artifact, "reused": False, "definition": definition}


def edit_artifact(
    db: Session, *, business: Business, actor: User, artifact_id: uuid.UUID, text: str
) -> AIOutputArtifact:
    """Manual edit: PENDING updates in place; APPROVED creates a new
    PENDING (manual approved content stays selected until approved again)."""
    artifact = get_artifact(db, business=business, artifact_id=artifact_id)
    text = (text or "").strip()
    if not text:
        raise ValidationError("edited text cannot be empty")
    if artifact.status == enums.AIArtifactStatus.PENDING_APPROVAL.value:
        artifact.generated_value = text
        db.flush()
        target = artifact
    elif artifact.status == enums.AIArtifactStatus.APPROVED.value:
        target = AIOutputArtifact(
            product_id=artifact.product_id,
            business_id=business.business_id,
            output_definition_id=artifact.output_definition_id,
            output_definition_key=artifact.output_definition_key,
            output_definition_version=artifact.output_definition_version,
            source_dependency_fingerprint=artifact.source_dependency_fingerprint,
            generated_value=text,
            status=enums.AIArtifactStatus.PENDING_APPROVAL.value,
            generated_at=_utcnow(),
            model="manual",
            provider="manual_edit",
        )
        db.add(target)
        db.flush()
    else:
        raise ConflictError(
            f"only PENDING or APPROVED artifacts can be edited (status: {artifact.status})"
        )
    AuditService(db).record(
        action="ai.artifact_edited",
        actor_user_id=actor.user_id,
        business_id=business.business_id,
        target_type="ai_artifact",
        target_id=str(target.artifact_id),
        meta={"parent": str(artifact.artifact_id), "product": str(artifact.product_id)},
    )
    return target


def approve_artifact(
    db: Session, *, business: Business, actor: User, artifact_id: uuid.UUID
) -> AIOutputArtifact:
    artifact = get_artifact(db, business=business, artifact_id=artifact_id)
    if artifact.status != enums.AIArtifactStatus.PENDING_APPROVAL.value:
        raise ConflictError("only PENDING artifacts can be approved")
    # Supersede any previous APPROVED for the same scope (explicit
    # regeneration/edit replaces the selected artifact).
    for other in db.scalars(
        select(AIOutputArtifact).where(
            AIOutputArtifact.product_id == artifact.product_id,
            AIOutputArtifact.output_definition_key == artifact.output_definition_key,
            AIOutputArtifact.output_definition_version == artifact.output_definition_version,
            AIOutputArtifact.source_dependency_fingerprint
            == artifact.source_dependency_fingerprint,
            AIOutputArtifact.status == enums.AIArtifactStatus.APPROVED.value,
        )
    ).all():
        other.status = enums.AIArtifactStatus.SUPERSEDED.value
    artifact.status = enums.AIArtifactStatus.APPROVED.value
    artifact.approved_value = artifact.generated_value
    artifact.approved_at = _utcnow()
    artifact.approved_by = actor.user_id
    db.flush()
    AuditService(db).record(
        action="ai.artifact_approved",
        actor_user_id=actor.user_id,
        business_id=business.business_id,
        target_type="ai_artifact",
        target_id=str(artifact.artifact_id),
        meta={"product": str(artifact.product_id)},
    )
    return artifact


def reject_artifact(
    db: Session, *, business: Business, actor: User, artifact_id: uuid.UUID
) -> AIOutputArtifact:
    """User-requested rejection: NO credit refund (AI spec section 12)."""
    artifact = get_artifact(db, business=business, artifact_id=artifact_id)
    if artifact.status != enums.AIArtifactStatus.PENDING_APPROVAL.value:
        raise ConflictError("only PENDING artifacts can be rejected")
    artifact.status = enums.AIArtifactStatus.REJECTED.value
    db.flush()
    AuditService(db).record(
        action="ai.artifact_rejected",
        actor_user_id=actor.user_id,
        business_id=business.business_id,
        target_type="ai_artifact",
        target_id=str(artifact.artifact_id),
        meta={"product": str(artifact.product_id)},
    )
    return artifact


def set_automatic(
    db: Session, *, business: Business, actor: User, enabled: bool
) -> Business:
    """Toggle the automatic-mode INTENT (trigger lands in Phase 8)."""
    business.ai_automatic_enabled = bool(enabled)
    db.flush()
    AuditService(db).record(
        action="ai.automatic_toggled",
        actor_user_id=actor.user_id,
        business_id=business.business_id,
        meta={"enabled": bool(enabled)},
    )
    return business
