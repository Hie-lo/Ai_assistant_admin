"""V1 permission matrix (business-scoped, least privilege).

Source: USER_BUSINESS_MEMBERSHIP_RBAC_SPEC_V1 section 12. Permission names
are the stable contract; UI and API both key off these strings.

Rules (enforced in ``app.application.authorization``):
- Effective access = role permission ∩ business policy  entitlement ∩ scope.
- OWNER has every permission.
- ADMIN gets a granted profile (default = all non-owner permissions).
- Owner-only permissions are never granted to ADMIN, ever.
"""

from __future__ import annotations

from app.domain.enums import MembershipRole

# --- Products ---
PRODUCTS_VIEW = "products.view"
PRODUCTS_PREVIEW = "products.preview"
PRODUCTS_MANAGE_MEDIA = "products.manage_media"
PRODUCTS_MANUAL_CHECK = "products.manual_check"

# --- Posts ---
POSTS_VIEW = "posts.view"
POSTS_CREATE_MANUAL = "posts.create_manual"
POSTS_PREVIEW = "posts.preview"
POSTS_PUBLISH = "posts.publish"
POSTS_UPDATE = "posts.update"
POSTS_REPOST = "posts.repost"
POSTS_DELETE_REMOTE = "posts.delete_remote"

# --- Scheduling / Sync ---
SYNC_VIEW = "sync.view"
SYNC_RUN_MANUAL = "sync.run_manual"
SCHEDULE_VIEW = "schedule.view"
SCHEDULE_MANAGE = "schedule.manage"

# --- AI ---
AI_VIEW = "ai.view"
AI_GENERATE = "ai.generate"
AI_RETRY = "ai.retry"
AI_EDIT_OUTPUT = "ai.edit_output"
AI_APPROVE_OUTPUT = "ai.approve_output"
AI_TOGGLE_AUTOMATIC = "ai.toggle_automatic"

# --- Connections ---
CONNECTIONS_VIEW = "connections.view"
CONNECTIONS_CONNECT = "connections.connect"
CONNECTIONS_RECONNECT = "connections.reconnect"
CONNECTIONS_DISCONNECT = "connections.disconnect"

# --- Business settings ---
BUSINESS_VIEW = "business.view"
BUSINESS_EDIT_SAFE_SETTINGS = "business.edit_safe_settings"

# --- Owner-only by default ---
SUBSCRIPTION_MANAGE = "subscription.manage"
BILLING_MANAGE = "billing.manage"
ADMIN_MANAGE = "admin.manage"
OWNER_TRANSFER = "owner.transfer"
SECURITY_RECOVERY = "security.recovery"
SENSITIVE_CONNECTION_CREDENTIALS = "sensitive_connection_credentials"
DESTRUCTIVE_BUSINESS_OPERATIONS = "destructive_business_operations"

#: Owner-only permissions. An ADMIN profile can NEVER contain these.
OWNER_ONLY_PERMISSIONS: frozenset[str] = frozenset(
    {
        SUBSCRIPTION_MANAGE,
        BILLING_MANAGE,
        ADMIN_MANAGE,
        OWNER_TRANSFER,
        SECURITY_RECOVERY,
        SENSITIVE_CONNECTION_CREDENTIALS,
        DESTRUCTIVE_BUSINESS_OPERATIONS,
    }
)

#: Every permission known to the system in V1.
ALL_PERMISSIONS: frozenset[str] = frozenset(
    {
        PRODUCTS_VIEW,
        PRODUCTS_PREVIEW,
        PRODUCTS_MANAGE_MEDIA,
        PRODUCTS_MANUAL_CHECK,
        POSTS_VIEW,
        POSTS_CREATE_MANUAL,
        POSTS_PREVIEW,
        POSTS_PUBLISH,
        POSTS_UPDATE,
        POSTS_REPOST,
        POSTS_DELETE_REMOTE,
        SYNC_VIEW,
        SYNC_RUN_MANUAL,
        SCHEDULE_VIEW,
        SCHEDULE_MANAGE,
        AI_VIEW,
        AI_GENERATE,
        AI_RETRY,
        AI_EDIT_OUTPUT,
        AI_APPROVE_OUTPUT,
        AI_TOGGLE_AUTOMATIC,
        CONNECTIONS_VIEW,
        CONNECTIONS_CONNECT,
        CONNECTIONS_RECONNECT,
        CONNECTIONS_DISCONNECT,
        BUSINESS_VIEW,
        BUSINESS_EDIT_SAFE_SETTINGS,
        SUBSCRIPTION_MANAGE,
        BILLING_MANAGE,
        ADMIN_MANAGE,
        OWNER_TRANSFER,
        SECURITY_RECOVERY,
        SENSITIVE_CONNECTION_CREDENTIALS,
        DESTRUCTIVE_BUSINESS_OPERATIONS,
    }
)


def owner_profile() -> frozenset[str]:
    """Every permission (the OWNER role)."""
    return ALL_PERMISSIONS


def admin_default_profile() -> frozenset[str]:
    """Default granted profile for a newly approved ADMIN.

    All non-owner permissions. The Owner may narrow this per membership at
    approval time, but can never widen it to include owner-only permissions.
    """
    return ALL_PERMISSIONS - OWNER_ONLY_PERMISSIONS


def resolve_profile(role: MembershipRole, granted: list[str] | None = None) -> frozenset[str]:
    """Resolve the effective permission set for a membership.

    - OWNER -> everything (granted list is ignored).
    - ADMIN -> the granted profile if provided, else the admin default.
    - Owner-only permissions are stripped from any non-owner profile, so a
      corrupt/over-granted stored list can never escalate privileges.
    """
    if role is MembershipRole.OWNER:
        return owner_profile()
    base = set(granted) if granted is not None else set(admin_default_profile())
    return frozenset(base - OWNER_ONLY_PERMISSIONS)
