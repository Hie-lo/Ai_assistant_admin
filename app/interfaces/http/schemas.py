"""Pydantic DTOs for the Phase 1 HTTP API."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

# Allows incidental leading/trailing whitespace (the service normalizes it);
# interior whitespace is rejected.
_EMAIL_PATTERN = r"^\s*[^@\s]+@[^@\s]+\.[^@\s]+\s*$"


# --- Auth ---


class RegisterRequest(BaseModel):
    email: str = Field(pattern=_EMAIL_PATTERN)
    password: str = Field(min_length=10, max_length=128)
    display_name: str = Field(min_length=1, max_length=120)


class LoginRequest(BaseModel):
    email: str = Field(pattern=_EMAIL_PATTERN)
    password: str = Field(min_length=1, max_length=128)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: uuid.UUID
    email: str
    display_name: str


class BusinessRef(BaseModel):
    business_id: uuid.UUID
    name: str
    business_type_key: str
    role: str


class MeOut(BaseModel):
    user: UserOut
    businesses: list[BusinessRef]


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    session_id: uuid.UUID
    user_agent: str | None
    ip: str | None
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    is_active: bool = False


# --- Business ---


class BusinessTypeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    name: str
    description: str | None


class CreateBusinessRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    business_type_key: str = Field(min_length=1, max_length=64)


class BusinessOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    business_id: uuid.UUID
    business_name: str
    business_type_key: str
    lifecycle_state: str
    created_at: datetime


class MemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    membership_id: uuid.UUID
    user_id: uuid.UUID
    role: str
    status: str
    approved_at: datetime | None


# --- Admin ---


class CreateInviteRequest(BaseModel):
    max_uses: int = Field(default=1, ge=1, le=10)


class InviteOut(BaseModel):
    invite_id: uuid.UUID
    code: str
    expires_at: datetime


class AdminRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    request_id: uuid.UUID
    candidate_user_id: uuid.UUID
    candidate_email: str | None = None
    candidate_name: str | None = None
    method: str
    status: str
    created_at: datetime
    resolved_at: datetime | None


class SubmitAdminRequest(BaseModel):
    method: str = Field(pattern=r"^(invite_code|channel_ref)$")
    code: str | None = Field(default=None, min_length=8, max_length=16)
    platform: str | None = Field(default=None, min_length=2, max_length=16)
    channel_id: str | None = Field(default=None, min_length=1, max_length=128)


class AdminRequestAccepted(BaseModel):
    request_id: uuid.UUID
    status: str
    created: bool


# --- Linking ---


class CreateLinkCodeRequest(BaseModel):
    platform: str = Field(min_length=2, max_length=16)


class LinkCodeOut(BaseModel):
    code: str
    platform: str
    expires_at: datetime


class IdentityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    identity_id: uuid.UUID
    platform: str
    platform_user_id: str
    verified_at: datetime


class VerifyLinkCodeRequest(BaseModel):
    platform: str = Field(min_length=2, max_length=16)
    code: str = Field(min_length=6, max_length=6)
    platform_user_id: str = Field(min_length=1, max_length=128)


class VerifyLinkCodeOut(BaseModel):
    user_id: uuid.UUID


# --- Phase 2: Plans / Subscriptions / Payments / Credits ---

_PLAN_CODE_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,39}$"


class PlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    plan_id: uuid.UUID
    code: str
    name: str
    currency: str
    price: int
    billing_period: str
    is_active: bool
    product_limit: int | None
    source_limit: int | None
    channel_limit: int | None
    sync_frequency_per_day: int | None
    ai_available: bool
    ai_monthly_credits: int
    product_preset_eligible: bool
    preset_customization: str
    report_level: str
    media_storage_limit_bytes: int | None
    admin_seat_limit: int | None
    feature_flags: dict


class PlanRequest(BaseModel):
    """Create or update a plan (operator). ``code`` is create-only."""

    code: str | None = Field(default=None, pattern=_PLAN_CODE_PATTERN)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    currency: str | None = Field(default=None, max_length=8)
    price: int | None = Field(default=None, ge=0)
    billing_period: str | None = "monthly"
    is_active: bool | None = None
    product_limit: int | None = Field(default=None, gt=0)
    source_limit: int | None = Field(default=None, gt=0)
    channel_limit: int | None = Field(default=None, gt=0)
    sync_frequency_per_day: int | None = Field(default=None, gt=0)
    ai_available: bool | None = None
    ai_monthly_credits: int | None = Field(default=None, ge=0)
    product_preset_eligible: bool | None = None
    preset_customization: str | None = None
    report_level: str | None = None
    media_storage_limit_bytes: int | None = Field(default=None, gt=0)
    admin_seat_limit: int | None = Field(default=None, gt=0)
    feature_flags: dict | None = None


class SubscriptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    subscription_id: uuid.UUID
    business_id: uuid.UUID
    # Computed from the joined plan rows (not ORM columns) — populated after
    # model_validate, hence the defaults.
    plan_code: str | None = None
    status: str
    pending_plan_code: str | None = None
    period_start: datetime | None
    period_end: datetime | None
    grace_end: datetime | None
    created_at: datetime
    updated_at: datetime


class SubscriptionRequest(BaseModel):
    plan_code: str = Field(min_length=1, max_length=40)


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    payment_id: uuid.UUID
    subscription_id: uuid.UUID
    purpose: str
    status: str
    expected_amount: int
    currency: str
    amount_paid: int | None
    reference: str | None
    note: str | None
    created_at: datetime
    verified_at: datetime | None


class PaymentVerifyRequest(BaseModel):
    amount_paid: int | None = Field(default=None, ge=0)
    reference: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=2000)


class PaymentRejectRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class ReasonRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class CreditPoolOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    pool_id: uuid.UUID
    pool_type: str
    period_label: str
    granted_total: int
    remaining: int


class CreditTransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    tx_id: uuid.UUID
    direction: str
    amount: int
    idempotency_key: str | None
    reference: str | None
    created_at: datetime


class CreditsOut(BaseModel):
    pools: list[CreditPoolOut]
    transactions: list[CreditTransactionOut]


class TopUpRequest(BaseModel):
    amount: int = Field(gt=0)
    note: str | None = Field(default=None, max_length=500)


class EntitlementsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    has_active: bool
    subscription_status: str | None
    plan_code: str | None
    product_limit: int | None
    source_limit: int | None
    channel_limit: int | None
    ai_available: bool
    ai_monthly_credits: int
    preset_customization: str
    report_level: str
    admin_seat_limit: int | None
    feature_flags: dict


class SubscriptionStatusOut(BaseModel):
    subscription: SubscriptionOut | None
    latest: SubscriptionOut | None
    pending_payment: PaymentOut | None
    entitlements: EntitlementsOut

# --- Phase 3: Sources / Products / Review cases ---


class SourceCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    kind: str = Field(pattern="^(EXCEL_UPLOAD|GOOGLE_SHEETS)$")
    external_ref: str | None = Field(default=None, max_length=320)
    sheet_name: str | None = Field(default=None, max_length=120)
    range_spec: str | None = Field(default=None, max_length=120)
    credentials_ref: str | None = Field(default=None, max_length=120)
    media_authoritative: bool = False


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source_id: uuid.UUID
    business_id: uuid.UUID
    name: str
    kind: str
    external_ref: str | None
    sheet_name: str | None
    range_spec: str | None
    status: str
    media_authoritative: bool
    last_sync_at: datetime | None
    last_baseline_count: int | None
    created_at: datetime
    updated_at: datetime


class MappingEntryIn(BaseModel):
    column: str = Field(min_length=1, max_length=220)
    canonical_field: str | None = None
    field_kind: str = Field(pattern="^(CORE|CUSTOM)$")
    field_type: str = Field(pattern="^(STRING|NUMBER|BOOLEAN|ENUM|PRICE|STOCK|MEDIA_URL)$")
    display_name: str = Field(min_length=1, max_length=120)
    required: bool = False
    template_exposed: bool = False
    confidence: float = 0.0
    evidence: str = ""


class MappingProposalRequest(BaseModel):
    entries: list[MappingEntryIn] = Field(min_length=1, max_length=200)


class MappingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    mapping_id: uuid.UUID
    source_id: uuid.UUID
    version: int
    status: str
    entries: list[dict]
    created_at: datetime


class ProductUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=320)
    description: str | None = None
    price: int | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=8)
    stock: int | None = Field(default=None, ge=0)
    category: str | None = Field(default=None, max_length=160)
    external_id: str | None = Field(default=None, max_length=120)
    sku: str | None = Field(default=None, max_length=120)
    barcode: str | None = Field(default=None, max_length=120)
    attributes: dict[str, str] | None = None


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    product_id: uuid.UUID
    business_id: uuid.UUID
    name: str
    category: str | None
    description: str | None
    price: int | None
    currency: str
    stock: int | None
    external_id: str | None
    sku: str | None
    barcode: str | None
    fingerprint: str | None
    lifecycle_state: str
    current_version: int
    attributes: dict
    identity_evidence: dict | None
    created_at: datetime
    updated_at: datetime


class ProductVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    version_id: uuid.UUID
    product_id: uuid.UUID
    version_no: int
    change_categories: list
    risk_level: str
    changed_fields: dict
    trigger: str
    created_at: datetime


class ProductMediaIn(BaseModel):
    url: str = Field(min_length=1, max_length=1024, pattern="^https?://")
    origin: str = Field(default="CUSTOMER", pattern="^(SOURCE|CUSTOMER)$")


class ProductMediaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    media_id: uuid.UUID
    product_id: uuid.UUID
    media_type: str
    origin: str
    url: str
    position: int
    status: str
    created_at: datetime


class ReviewCaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    case_id: uuid.UUID
    business_id: uuid.UUID
    kind: str
    product_id: uuid.UUID | None
    source_id: uuid.UUID | None
    payload: dict
    status: str
    resolution: str | None
    resolved_at: datetime | None
    created_at: datetime


class ReviewCaseResolveRequest(BaseModel):
    action: str = Field(
        pattern="^(MERGE_TO|CREATE_NEW|KEEP_EXISTING|REASSIGN_ID"
        "|APPLY_BY_RESYNC|ACKNOWLEDGE|DISMISS)$"
    )
    target_product_id: uuid.UUID | None = None


class ImportRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    run_id: uuid.UUID
    source_id: uuid.UUID
    mapping_version_id: uuid.UUID | None
    trigger: str
    status: str
    counts: dict
    row_errors: list
    failure_summary: str | None
    started_at: datetime
    finished_at: datetime | None


# --- Phase 4: Content / Presets / AI ---


class PresetBlockIn(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    type: str = Field(min_length=1, max_length=32)
    ownership: str = Field(default="STATIC", min_length=1, max_length=32)
    payload: dict = Field(default_factory=dict)


class PresetCreateRequest(BaseModel):
    business_type_key: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=160)
    description: str | None = None
    is_default: bool = False
    blocks: list[PresetBlockIn] = Field(min_length=1, max_length=64)


class PresetVersionRequest(BaseModel):
    blocks: list[PresetBlockIn] = Field(min_length=1, max_length=64)


class PresetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    preset_id: uuid.UUID
    business_type_key: str
    name: str
    description: str | None
    is_default: bool
    created_at: datetime


class PresetVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    version_id: uuid.UUID
    preset_id: uuid.UUID
    version: int
    blocks: list
    content_hash: str
    status: str
    created_at: datetime


class PresetDetailOut(PresetOut):
    versions: list[PresetVersionOut] = []
    active_version: int | None = None


class BusinessTypePresetOut(BaseModel):
    preset_id: uuid.UUID
    name: str
    description: str | None
    is_default: bool
    active_version: int | None


# --- Per-product presets (top-plan entitlement) ---


class ProductPresetCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str | None = None
    blocks: list[PresetBlockIn] = Field(min_length=1, max_length=64)


class ProductPresetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    product_preset_id: uuid.UUID
    business_id: uuid.UUID
    name: str
    description: str | None
    created_at: datetime


class ProductPresetVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    version_id: uuid.UUID
    product_preset_id: uuid.UUID
    version: int
    blocks: list
    content_hash: str
    status: str
    created_at: datetime


class ProductPresetAssignRequest(BaseModel):
    product_preset_id: uuid.UUID | None = None


# --- AI definitions (platform operator) ---


class AIDefinitionCreateRequest(BaseModel):
    key: str = Field(min_length=3, max_length=64)
    display_name: str = Field(min_length=1, max_length=120)
    prompt_template: str = Field(min_length=1)
    input_fields: list[str] = Field(default_factory=list, max_length=16)
    max_output_length: int = Field(default=800, ge=1, le=4096)
    cost_credits: int = Field(default=1, ge=1, le=100)
    active: bool = True


class AIDefinitionVersionRequest(BaseModel):
    prompt_template: str | None = None
    input_fields: list[str] | None = Field(default=None, max_length=16)
    max_output_length: int | None = Field(default=None, ge=1, le=4096)
    cost_credits: int | None = Field(default=None, ge=1, le=100)
    activate: bool = True


class AIDefinitionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    definition_id: uuid.UUID
    key: str
    version: int
    display_name: str
    prompt_template: str
    input_fields: list
    max_output_length: int
    cost_credits: int
    active: bool
    created_at: datetime


# --- AI artifacts (business) ---


class AIGenerateRequest(BaseModel):
    definition_key: str = Field(min_length=3, max_length=64)


class AIArtifactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    artifact_id: uuid.UUID
    product_id: uuid.UUID
    business_id: uuid.UUID
    output_definition_key: str
    output_definition_version: int
    source_dependency_fingerprint: str
    generated_value: str
    approved_value: str | None
    status: str
    model: str | None
    provider: str | None
    generated_at: datetime | None
    approved_at: datetime | None
    created_at: datetime


class AIGenerateResult(BaseModel):
    artifact: AIArtifactOut
    reused: bool
    definition_key: str
    definition_version: int


class AIArtifactEditRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4096)


class AIToggleAutomaticRequest(BaseModel):
    enabled: bool


class PreviewBlockOut(BaseModel):
    id: str
    type: str
    ownership: str
    priority: int
    text: str
    kept: bool


class PreviewOut(BaseModel):
    product_id: uuid.UUID
    source: str
    preset_id: uuid.UUID | None
    preset_name: str | None
    preset_version: int | None
    text: str
    total_chars: int
    max_length: int
    fits: bool
    blocked_reason: str | None
    media_urls: list[str]
    blocks: list[PreviewBlockOut]
    warnings: list[str]
