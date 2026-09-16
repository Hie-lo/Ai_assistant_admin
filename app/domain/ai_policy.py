"""AI generation policy (Phase 4 domain, pure).

Encodes the approved V1 decisions (owner-confirmed 2026-09-13) and the AI
configuration spec:
- bounded retries (3 attempts, no loops) for transient failures;
- reuse of an APPROVED artifact while the declared inputs and the
  definition version are unchanged (spec section 6: a prompt/model change
  must never auto-regenerate existing content);
- refund policy: infrastructure/permanent failures that produce no usable
  artifact are refundable; a stored artifact that the user later rejects
  is NOT refunded (user-requested rejection, spec section 12);
- automatic mode eligibility (spec section 9): only eligible
  new/unsatisfied content; existing approved output is reused.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

#: Bounded retry budget for transient provider failures (spec section 10).
MAX_ATTEMPTS = 3

#: Backoff bases (seconds) for attempts 1, 2 — applied by the caller.
BACKOFF_SECONDS = (1.0, 2.0)


def dependency_fingerprint(
    definition_key: str,
    definition_version: int,
    inputs: dict[str, str],
) -> str:
    """Stable fingerprint of the declared inputs for reuse decisions.

    Only the fields the definition DECLARED participate, so an unrelated
    product edit (e.g. price) does not invalidate a description artifact.
    """
    canonical = json.dumps(
        {"k": definition_key, "v": definition_version, "i": inputs},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ArtifactRecord:
    """Minimal artifact view needed for policy decisions."""

    status: str  # AIArtifactStatus value
    definition_version: int
    dependency_fingerprint: str


def reuse_eligible(
    existing: ArtifactRecord | None,
    *,
    definition_version: int,
    fingerprint: str,
) -> bool:
    """True when an existing artifact must be reused (no new generation).

    Reuse requires: APPROVED + same definition version + same declared
    inputs. A DRAFT-era or different-version artifact never masks the need
    for a fresh generation.
    """
    if existing is None:
        return False
    return (
        existing.status == "APPROVED"
        and existing.definition_version == definition_version
        and existing.dependency_fingerprint == fingerprint
    )


#: Failure kinds that consume a retry attempt (both bounded; AI spec 10-11).
_RETRYABLE_KINDS = frozenset({"TRANSIENT", "INVALID_OUTPUT"})


def should_retry(failure_kind: str, attempts_made: int) -> bool:
    """Bounded retry: transient/invalid-output failures, MAX_ATTEMPTS total.

    Permanent provider failures are never retried (no loop — developer
    directive rule 11).
    """
    if failure_kind not in _RETRYABLE_KINDS:
        return False
    return attempts_made < MAX_ATTEMPTS


def should_refund(artifact_created: bool) -> bool:
    """Refund the consumed credit when no usable artifact was produced.

    - provider/infrastructure failure (nothing stored) -> refund
    - permanent provider failure (nothing stored) -> refund
    - invalid output after all attempts (nothing stored) -> refund
    - artifact stored, user later rejects it -> NO refund (spec section 12)
    """
    return not artifact_created


@dataclass(frozen=True)
class AutoEligibility:
    eligible: bool
    reason: str


def auto_generation_eligible(
    *,
    plan_ai_available: bool,
    credits_available: int,
    business_automatic_enabled: bool,
    has_valid_approved: bool,
    has_pending_or_rejected_only: bool,
) -> AutoEligibility:
    """Eligibility for automatic generation (spec section 9).

    Only NEW/unsatisfied content is eligible: an already-approved,
    still-valid artifact is reused, never regenerated. Historical published
    posts never trigger automatic regeneration (enforced by the caller
    only invoking this for unsatisfied content).
    """
    if not business_automatic_enabled:
        return AutoEligibility(False, "automatic mode is off for this business")
    if not plan_ai_available:
        return AutoEligibility(False, "plan does not include AI")
    if has_valid_approved:
        return AutoEligibility(False, "existing approved output is reused")
    if credits_available <= 0:
        return AutoEligibility(False, "no AI credits remaining")
    if not has_pending_or_rejected_only:
        return AutoEligibility(False, "no new/unsatisfied content")
    return AutoEligibility(True, "eligible")


def validate_generated_text(text: str, *, max_length: int) -> str | None:
    """Validate provider output (spec section 11). Returns an error or None.

    Rules: non-empty, within max_length, printable (no NUL/other C0 control
    characters other than \\n and \\t), no leading/trailing junk markers.
    """
    if text is None:
        return "empty output"
    stripped = text.strip()
    if not stripped:
        return "empty output"
    if len(stripped) > max_length:
        return f"output exceeds max length ({len(stripped)} > {max_length})"
    for ch in stripped:
        if ord(ch) < 32 and ch not in "\n\t":
            return "output contains control characters"
    return None
