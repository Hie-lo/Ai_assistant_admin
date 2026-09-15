"""AI output definition registry (Phase 4, platform-operator managed).

AI outputs are CONFIGURABLE definitions, not hard-coded functions (AI
spec sections 2-3): key, display name, prompt template, declared input
fields, max output length, cost credits, retry policy. Changes create a
NEW version (section 15): historical artifacts keep referencing the
version that produced them; activation of a new version never regenerates
existing content (reuse rule, section 6).
"""

from __future__ import annotations

import re
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application.audit import AuditService
from app.domain.errors import ConflictError, NotFoundError, ValidationError
from app.infrastructure.db.models import AIOutputDefinition, User

_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")

#: Default seed definitions (AI spec section 2 example keys).
SEED_DEFINITIONS: list[dict] = [
    {
        "key": "ai_description",
        "display_name": "توضیح کالا",
        "prompt_template": (
            "برای کالای زیر یک توضیح کوتاه و طبیعی به فارسی بنویس.\n"
            "نام: {name}\nدسته: {category}\nتوضیح منبع: {description}\nقیمت: {price} {currency}\n"
            "فقط متن خروجی را برگردان."
        ),
        "input_fields": ["name", "category", "description", "price", "currency"],
        "max_output_length": 800,
        "cost_credits": 1,
    },
    {
        "key": "ai_short_title",
        "display_name": "عنوان کوتاه",
        "prompt_template": (
            "برای کالای زیر یک عنوان کوتاه (حداکثر 80 کاراکتر) به فارسی بنویس.\n"
            "نام: {name}\nدسته: {category}\nفقط عنوان را برگردان."
        ),
        "input_fields": ["name", "category"],
        "max_output_length": 120,
        "cost_credits": 1,
    },
]


def _require_key(key: str) -> str:
    key = (key or "").strip().lower()
    if not _KEY_RE.match(key):
        raise ValidationError(
            "definition key must be 3-64 chars, lowercase a-z0-9_ (e.g. ai_description)"
        )
    return key


def list_definitions(db: Session, *, include_inactive: bool = True) -> list[AIOutputDefinition]:
    stmt = select(AIOutputDefinition).order_by(AIOutputDefinition.key, AIOutputDefinition.version)
    if not include_inactive:
        stmt = stmt.where(AIOutputDefinition.active.is_(True))
    return list(db.scalars(stmt).all())


def get_active_definition(db: Session, key: str) -> AIOutputDefinition:
    row = db.scalars(
        select(AIOutputDefinition).where(
            AIOutputDefinition.key == _require_key(key),
            AIOutputDefinition.active.is_(True),
        )
    ).first()
    if row is None:
        raise NotFoundError(f"no active AI output definition '{key}'")
    return row


def get_definition(
    db: Session, *, definition_id: uuid.UUID
) -> AIOutputDefinition:
    row = db.get(AIOutputDefinition, definition_id)
    if row is None:
        raise NotFoundError("AI output definition not found")
    return row


def create_definition(
    db: Session,
    *,
    actor: User,
    key: str,
    display_name: str,
    prompt_template: str,
    input_fields: list[str],
    max_output_length: int = 800,
    cost_credits: int = 1,
    active: bool = True,
) -> AIOutputDefinition:
    key = _require_key(key)
    existing = db.scalars(select(AIOutputDefinition).where(AIOutputDefinition.key == key)).first()
    if existing is not None:
        raise ConflictError(f"AI output definition '{key}' already exists")
    display_name = (display_name or "").strip()
    if not 1 <= len(display_name) <= 120:
        raise ValidationError("display name (1-120 chars) is required")
    prompt_template = (prompt_template or "").strip()
    if not prompt_template:
        raise ValidationError("prompt_template is required")
    if not 1 <= max_output_length <= 4096:
        raise ValidationError("max_output_length must be 1-4096")
    if not 1 <= cost_credits <= 100:
        raise ValidationError("cost_credits must be 1-100")
    fields = [f.strip() for f in (input_fields or []) if f.strip()]
    if len(fields) > 16:
        raise ValidationError("at most 16 input fields")
    row = AIOutputDefinition(
        key=key,
        version=1,
        display_name=display_name,
        prompt_template=prompt_template,
        input_fields=fields,
        max_output_length=max_output_length,
        cost_credits=cost_credits,
        active=active,
        created_by=actor.user_id,
    )
    db.add(row)
    db.flush()
    AuditService(db).record(
        action="ai_definition.created",
        actor_user_id=actor.user_id,
        target_type="ai_definition",
        target_id=key,
        meta={"version": 1, "active": active},
    )
    return row


def create_definition_version(
    db: Session,
    *,
    actor: User,
    key: str,
    prompt_template: str,
    input_fields: list[str],
    max_output_length: int | None = None,
    cost_credits: int | None = None,
    activate: bool = True,
) -> AIOutputDefinition:
    """A new version of an existing key (section 15: auditable versions)."""
    key = _require_key(key)
    versions = db.scalars(
        select(AIOutputDefinition).where(AIOutputDefinition.key == key).order_by(
            AIOutputDefinition.version
        )
    ).all()
    if not versions:
        raise NotFoundError(f"AI output definition '{key}' does not exist")
    last = versions[-1]
    prompt_template = (prompt_template or last.prompt_template).strip()
    if not prompt_template:
        raise ValidationError("prompt_template is required")
    fields = [
        f.strip()
        for f in (input_fields if input_fields is not None else last.input_fields or [])
        if f.strip()
    ]
    if len(fields) > 16:
        raise ValidationError("at most 16 input fields")
    if activate:
        for v in versions:
            v.active = False
    row = AIOutputDefinition(
        key=key,
        version=last.version + 1,
        display_name=last.display_name,
        prompt_template=prompt_template,
        input_fields=fields,
        max_output_length=(
            max_output_length
            if max_output_length is not None
            else last.max_output_length
        ),
        cost_credits=cost_credits if cost_credits is not None else last.cost_credits,
        provider_policy=last.provider_policy,
        retry_policy=last.retry_policy,
        allowed_contexts=last.allowed_contexts,
        active=activate,
        created_by=actor.user_id,
    )
    db.add(row)
    db.flush()
    AuditService(db).record(
        action="ai_definition.version_created",
        actor_user_id=actor.user_id,
        target_type="ai_definition",
        target_id=key,
        meta={"version": row.version, "activated": activate},
    )
    return row


def seed_default_definitions(db: Session, *, actor: User | None = None) -> int:
    """Idempotent seed of the default definitions (returns rows created)."""
    created = 0
    for spec in SEED_DEFINITIONS:
        exists = db.scalars(
            select(AIOutputDefinition).where(AIOutputDefinition.key == spec["key"])
        ).first()
        if exists is not None:
            continue
        create_definition(
            db,
            actor=actor,  # type: ignore[arg-type]
            key=spec["key"],
            display_name=spec["display_name"],
            prompt_template=spec["prompt_template"],
            input_fields=spec["input_fields"],
            max_output_length=spec["max_output_length"],
            cost_credits=spec["cost_credits"],
            active=True,
        )
        created += 1
    return created
