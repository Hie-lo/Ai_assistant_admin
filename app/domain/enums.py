"""Domain enumerations (framework-agnostic).

These are the canonical state/role vocabularies referenced across the RBAC,
auth, and audit contracts. ORM models in ``app.infrastructure.db.models``
use plain strings for storage and validate against these enums.
"""

from __future__ import annotations

from enum import StrEnum


class str_enum(StrEnum):
    """Enum whose members are also plain strings (value == storage form)."""


class AccountStatus(str_enum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    DELETED = "DELETED"


class BusinessLifecycle(str_enum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    ARCHIVED = "ARCHIVED"


class MembershipRole(str_enum):
    OWNER = "OWNER"
    ADMIN = "ADMIN"


class MembershipStatus(str_enum):
    PENDING_REQUEST = "PENDING_REQUEST"
    ACTIVE = "ACTIVE"
    REJECTED = "REJECTED"
    REVOKED = "REVOKED"
    SUSPENDED = "SUSPENDED"


class AdminRequestMethod(str_enum):
    INVITE_CODE = "INVITE_CODE"
    CHANNEL_REF = "CHANNEL_REF"


class AdminRequestStatus(str_enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    SUPERSEDED = "SUPERSEDED"


class Platform(str_enum):
    WEB = "WEB"
    TELEGRAM = "TELEGRAM"
    BALE = "BALE"
    EITAA = "EITAA"
    RUBIKA = "RUBIKA"


#: Platforms usable as a channel target for publication / channel links.
CHANNEL_PLATFORMS: frozenset[Platform] = frozenset(
    {Platform.TELEGRAM, Platform.BALE, Platform.EITAA, Platform.RUBIKA}
)


class ChannelLinkStatus(str_enum):
    LINKED = "LINKED"
    PENDING_VERIFICATION = "PENDING_VERIFICATION"
    UNLINKED = "UNLINKED"


class AuditOutcome(str_enum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
