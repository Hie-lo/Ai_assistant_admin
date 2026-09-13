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
