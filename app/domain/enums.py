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


class BillingPeriod(str_enum):
    MONTHLY = "monthly"


class SubscriptionStatus(str_enum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    GRACE = "GRACE"
    EXPIRED = "EXPIRED"
    SUSPENDED = "SUSPENDED"
    CANCELLED = "CANCELLED"
    REFUNDED = "REFUNDED"


class PaymentStatus(str_enum):
    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"


class PaymentPurpose(str_enum):
    NEW = "NEW"
    RENEWAL = "RENEWAL"
    PLAN_CHANGE = "PLAN_CHANGE"


class CreditPoolType(str_enum):
    MONTHLY = "MONTHLY"
    PURCHASED = "PURCHASED"


class CreditDirection(str_enum):
    GRANT = "GRANT"
    CONSUME = "CONSUME"
    REFUND = "REFUND"
    EXPIRE = "EXPIRE"


class PresetCustomizationLevel(str_enum):
    NONE = "none"
    BASIC = "basic"
    FULL = "full"


class ReportLevel(str_enum):
    NONE = "none"
    BASIC = "basic"
    FULL = "full"


#: Pool period label for non-expiring purchased credit pools.
PURCHASED_PERIOD_LABEL = "LIFETIME"


#: Subscription statuses that are "live" (block a second concurrent one per
#: business and are what an entitlement can be active under).
SUBSCRIPTION_NON_TERMINAL: frozenset[SubscriptionStatus] = frozenset(
    {
        SubscriptionStatus.PENDING,
        SubscriptionStatus.ACTIVE,
        SubscriptionStatus.GRACE,
        SubscriptionStatus.SUSPENDED,
    }
)


class AuditOutcome(str_enum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"


# --- Phase 3: Source / Product ---------------------------------------------


class ProductLifecycle(str_enum):
    # V1 active states. DISCOVERED / SUSPICIOUS_CHANGE / RESTORED are reserved
    # for later phases (documented in the approved design, 2026-09-13).
    ACTIVE = "ACTIVE"
    MISSING_FROM_SOURCE = "MISSING_FROM_SOURCE"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    SOURCE_INVALID = "SOURCE_INVALID"
    ARCHIVED = "ARCHIVED"
    DISCOVERED = "DISCOVERED"


class SourceKind(str_enum):
    EXCEL_UPLOAD = "EXCEL_UPLOAD"
    GOOGLE_SHEETS = "GOOGLE_SHEETS"


class SourceStatus(str_enum):
    PENDING_MAPPING = "PENDING_MAPPING"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    ERROR = "ERROR"


class MappingStatus(str_enum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"


class ImportRunStatus(str_enum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    SUCCEEDED_WITH_ERRORS = "SUCCEEDED_WITH_ERRORS"
    FAILED = "FAILED"
    PREVIEW = "PREVIEW"


class NotificationStatus(str_enum):
    UNREAD = "UNREAD"
    READ = "READ"


class NotificationKind(str_enum):
    SYNC_RETRY_EXHAUSTED = "SYNC_RETRY_EXHAUSTED"
    SYNC_RECOVERY_REQUIRED = "SYNC_RECOVERY_REQUIRED"


class SyncJobStatus(str_enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    RETRY_WAITING = "RETRY_WAITING"
    SUCCEEDED = "SUCCEEDED"
    SUCCEEDED_WITH_ERRORS = "SUCCEEDED_WITH_ERRORS"
    FAILED_RETRY_EXHAUSTED = "FAILED_RETRY_EXHAUSTED"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    CANCELLED = "CANCELLED"


class RowOutcome(str_enum):
    UNCHANGED = "UNCHANGED"
    VALID = "VALID"
    WARNING = "WARNING"
    BLOCKED = "BLOCKED"
    DUPLICATE_CANDIDATE = "DUPLICATE_CANDIDATE"
    IDENTITY_AMBIGUOUS = "IDENTITY_AMBIGUOUS"
    IDENTITY_CONFLICT = "IDENTITY_CONFLICT"
    INVALID_REQUIRED_FIELD = "INVALID_REQUIRED_FIELD"
    INVALID_TYPE = "INVALID_TYPE"
    INVALID_MEDIA = "INVALID_MEDIA"


class IdentityOutcome(str_enum):
    EXACT_MATCH = "EXACT_MATCH"
    CONFIDENT_MATCH = "CONFIDENT_MATCH"
    NEW_PRODUCT = "NEW_PRODUCT"
    POSSIBLE_DUPLICATE = "POSSIBLE_DUPLICATE"
    AMBIGUOUS = "AMBIGUOUS"
    UNRESOLVED = "UNRESOLVED"
    IDENTITY_CONFLICT = "IDENTITY_CONFLICT"


class ReviewCaseKind(str_enum):
    IDENTITY_AMBIGUOUS = "IDENTITY_AMBIGUOUS"
    IDENTITY_CONFLICT = "IDENTITY_CONFLICT"
    DUPLICATE_CANDIDATE = "DUPLICATE_CANDIDATE"
    SUSPICIOUS_CHANGE = "SUSPICIOUS_CHANGE"
    MASS_MISSING_BLOCKED = "MASS_MISSING_BLOCKED"


class ReviewCaseStatus(str_enum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    DISMISSED = "DISMISSED"


class ChangeRisk(str_enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ChangeCategory(str_enum):
    NAME_CHANGED = "NAME_CHANGED"
    PRICE_CHANGED = "PRICE_CHANGED"
    STOCK_CHANGED = "STOCK_CHANGED"
    DESCRIPTION_CHANGED = "DESCRIPTION_CHANGED"
    MEDIA_CHANGED = "MEDIA_CHANGED"
    SPECIFICATION_CHANGED = "SPECIFICATION_CHANGED"
    CATEGORY_CHANGED = "CATEGORY_CHANGED"
    CUSTOM_FIELD_CHANGED = "CUSTOM_FIELD_CHANGED"
    IDENTITY_IDENTIFIER_CHANGED = "IDENTITY_IDENTIFIER_CHANGED"


class MediaOrigin(str_enum):
    SOURCE = "SOURCE"
    CUSTOMER = "CUSTOMER"


class MediaStatus(str_enum):
    ACTIVE = "ACTIVE"
    REMOVED = "REMOVED"


class FieldKind(str_enum):
    CORE = "CORE"
    CUSTOM = "CUSTOM"


class FieldType(str_enum):
    STRING = "STRING"
    NUMBER = "NUMBER"
    BOOLEAN = "BOOLEAN"
    ENUM = "ENUM"
    PRICE = "PRICE"
    STOCK = "STOCK"
    MEDIA_URL = "MEDIA_URL"


class SyncTrigger(str_enum):
    MANUAL = "MANUAL"
    SCHEDULED = "SCHEDULED"
    EVENT_ASSISTED = "EVENT_ASSISTED"


# --- Content / AI (Phase 4) ---


class PresetVersionStatus(str_enum):
    """Lifecycle of one preset version (mirrors source mapping versions)."""

    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"


class BlockType(str_enum):
    """Allowlisted content block types (Post & Publication spec section 4)."""

    STATIC_TEXT = "STATIC_TEXT"
    PRODUCT_FIELD = "PRODUCT_FIELD"
    CUSTOM_FIELD = "CUSTOM_FIELD"
    AI_OUTPUT = "AI_OUTPUT"
    HASHTAG_SET = "HASHTAG_SET"
    CONTACT = "CONTACT"
    DATE = "DATE"
    MEDIA_REFERENCE = "MEDIA_REFERENCE"
    SEPARATOR = "SEPARATOR"


class BlockOwnership(str_enum):
    """Managed vs non-managed content classification (spec section 5)."""

    SYSTEM_MANAGED = "SYSTEM_MANAGED"
    CUSTOMER_MANAGED = "CUSTOMER_MANAGED"
    STATIC = "STATIC"
    DERIVED = "DERIVED"


class AIArtifactStatus(str_enum):
    """AI output artifact lifecycle (AI spec sections 5-7)."""

    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"


class AIFailureKind(str_enum):
    """Classification of a failed generation (drives refund + retry policy)."""

    TRANSIENT = "TRANSIENT"
    PERMANENT = "PERMANENT"
    INVALID_OUTPUT = "INVALID_OUTPUT"


# --- Publication / Platform connections (Phase 5) ---


class PlatformConnectionStatus(str_enum):
    """Lifecycle of a business's connection to a platform target."""

    PENDING_VERIFICATION = "PENDING_VERIFICATION"
    VERIFIED = "VERIFIED"
    PERMISSION_LOST = "PERMISSION_LOST"
    DISCONNECTED = "DISCONNECTED"


class PostStatus(str_enum):
    """Logical Post state (platform-independent; publication state lives on
    Publication rows)."""

    PUBLISHED = "PUBLISHED"
    UPDATE_REQUIRED = "UPDATE_REQUIRED"
    REPOST_REQUIRED = "REPOST_REQUIRED"
    DELETE_REQUIRED = "DELETE_REQUIRED"
    ARCHIVED = "ARCHIVED"


class PublicationStatus(str_enum):
    """Explicit publication state machine (a boolean `posted` is never the
    source of truth — spec section 12)."""

    NOT_PUBLISHED = "NOT_PUBLISHED"
    QUEUED = "QUEUED"
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    UPDATE_PENDING = "UPDATE_PENDING"
    UPDATING = "UPDATING"
    REPOST_PENDING = "REPOST_PENDING"
    REPOSTING = "REPOSTING"
    DELETE_PENDING = "DELETE_PENDING"
    DELETING = "DELETING"
    REMOTE_DELETED = "REMOTE_DELETED"
    DISCONNECTED = "DISCONNECTED"
    PERMISSION_LOST = "PERMISSION_LOST"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_FINAL = "FAILED_FINAL"
    UNKNOWN_REMOTE_STATE = "UNKNOWN_REMOTE_STATE"
    RECONCILING = "RECONCILING"


class PublicationOperation(str_enum):
    """Semantic operations recorded on attempts."""

    PUBLISH = "PUBLISH"
    EDIT = "EDIT"
    REPOST = "REPOST"
    DELETE = "DELETE"
    RECONCILE = "RECONCILE"
    VERIFY_CONNECTION = "VERIFY_CONNECTION"


class AttemptStatus(str_enum):
    """Outcome of one remote operation attempt."""

    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"  # e.g. timeout after possible acceptance


class PublicationErrorCode(str_enum):
    """Machine-readable error taxonomy (spec section 29)."""

    AUTHENTICATION_ERROR = "AUTHENTICATION_ERROR"
    PERMISSION_ERROR = "PERMISSION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    RATE_LIMITED = "RATE_LIMITED"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    LENGTH_LIMIT = "LENGTH_LIMIT"
    MEDIA_UNSUPPORTED = "MEDIA_UNSUPPORTED"
    PLATFORM_UNSUPPORTED_OPERATION = "PLATFORM_UNSUPPORTED_OPERATION"
    NETWORK_TIMEOUT = "NETWORK_TIMEOUT"
    NETWORK_ERROR = "NETWORK_ERROR"
    REMOTE_UNKNOWN = "REMOTE_UNKNOWN"
    DUPLICATE_GUARD = "DUPLICATE_GUARD"
    CONCURRENCY_CONFLICT = "CONCURRENCY_CONFLICT"
    INTERNAL_ERROR = "INTERNAL_ERROR"
