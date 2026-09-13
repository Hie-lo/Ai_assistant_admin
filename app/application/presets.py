"""Business-type preset service (Phase 4, platform-operator managed).

Presets are structured, versioned and platform-neutral (master spec
section 9; Post & Publication spec section 4). Historical versions are
never mutated (developer directive: historical preset versions do not
mutate silently).

Authorization: creation/versioning/activation/default are PLATFORM
OPERATOR (super admin) actions; business members read only the presets of
their own business type (routes enforce this).
"""

from __future__ import annotations

import hashlib
import json
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application.audit import AuditService
from app.domain import enums
from app.domain.content import (
    ContentBlock,
    ContentError,
    RenderContext,
    render_blocks,
    validate_blocks,
)
from app.domain.errors import ConflictError, NotFoundError, ValidationError
from app.infrastructure.db.models import (
    AIOutputDefinition,
    BusinessType,
    Preset,
    PresetVersion,
    User,
)

_PRES = "preset"


def _content_hash(blocks: list[dict]) -> str:
    canonical = json.dumps(blocks, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _active_ai_keys(db: Session) -> frozenset[str]:
    return frozenset(
        db.scalars(
            select(AIOutputDefinition.key).where(AIOutputDefinition.active.is_(True))
        ).all()
    )


def _require_business_type(db: Session, key: str) -> BusinessType:
    bt = db.get(BusinessType, key)
    if bt is None or not bt.is_active:
        raise NotFoundError(f"unknown or inactive business type: {key}")
    return bt


def list_presets(db: Session) -> list[Preset]:
    return list(
        db.scalars(select(Preset).order_by(Preset.business_type_key, Preset.name)).all()
    )


def get_preset(db: Session, preset_id: uuid.UUID) -> Preset:
    preset = db.get(Preset, preset_id)
    if preset is None:
        raise NotFoundError("preset not found")
    return preset


def get_preset_version(db: Session, preset_id: uuid.UUID, version: int) -> PresetVersion:
    row = db.scalars(
        select(PresetVersion).where(
            PresetVersion.preset_id == preset_id,
            PresetVersion.version == version,
        )
    ).first()
    if row is None:
        raise NotFoundError(f"preset version {version} not found")
    return row


def _versions(db: Session, preset_id: uuid.UUID) -> list[PresetVersion]:
    return list(
        db.scalars(
            select(PresetVersion)
            .where(PresetVersion.preset_id == preset_id)
            .order_by(PresetVersion.version)
        ).all()
    )


def active_version(db: Session, preset: Preset) -> PresetVersion | None:
    for v in _versions(db, preset.preset_id):
        if v.status == enums.PresetVersionStatus.ACTIVE.value:
            return v
    return None


def create_preset(
    db: Session,
    *,
    actor: User,
    business_type_key: str,
    name: str,
    description: str | None,
    is_default: bool,
    blocks: list[dict],
) -> Preset:
    _require_business_type(db, business_type_key)
    name = (name or "").strip()
    if not 1 <= len(name) <= 160:
        raise ValidationError("preset name (1-160 chars) is required")
    try:
        parsed = validate_blocks(blocks, _active_ai_keys(db))
    except ContentError as exc:
        raise ValidationError(str(exc)) from exc
    existing = db.scalars(
        select(Preset).where(
            Preset.business_type_key == business_type_key,
            Preset.name == name,
        )
    ).first()
    if existing is not None:
        raise ConflictError("a preset with this name exists for the business type")

    preset = Preset(
        business_type_key=business_type_key,
        name=name,
        description=(description or "").strip() or None,
        is_default=False,
        created_by=actor.user_id,
    )
    db.add(preset)
    db.flush()
    version = PresetVersion(
        preset_id=preset.preset_id,
        version=1,
        blocks=[b.to_dict() for b in parsed],
        content_hash=_content_hash([b.to_dict() for b in parsed]),
        status=enums.PresetVersionStatus.ACTIVE.value,
        created_by=actor.user_id,
    )
    db.add(version)
    db.flush()
    if is_default:
        _set_default(db, preset)
    AuditService(db).record(
        action="preset.created",
        actor_user_id=actor.user_id,
        target_type=_PRES,
        target_id=str(preset.preset_id),
        meta={"business_type": business_type_key, "is_default": is_default},
    )
    return preset


def propose_version(
    db: Session, *, actor: User, preset_id: uuid.UUID, blocks: list[dict]
) -> PresetVersion:
    preset = get_preset(db, preset_id)
    try:
        parsed = validate_blocks(blocks, _active_ai_keys(db))
    except ContentError as exc:
        raise ValidationError(str(exc)) from exc
    versions = _versions(db, preset.preset_id)
    next_no = (versions[-1].version if versions else 0) + 1
    version = PresetVersion(
        preset_id=preset.preset_id,
        version=next_no,
        blocks=[b.to_dict() for b in parsed],
        content_hash=_content_hash([b.to_dict() for b in parsed]),
        status=enums.PresetVersionStatus.DRAFT.value,
        created_by=actor.user_id,
    )
    db.add(version)
    db.flush()
    AuditService(db).record(
        action="preset.version_proposed",
        actor_user_id=actor.user_id,
        target_type=_PRES,
        target_id=str(preset.preset_id),
        meta={"version": next_no},
    )
    return version


def activate_version(
    db: Session, *, actor: User, preset_id: uuid.UUID, version_no: int
) -> PresetVersion:
    preset = get_preset(db, preset_id)
    version = get_preset_version(db, preset_id, version_no)
    if version.status != enums.PresetVersionStatus.DRAFT.value:
        raise ConflictError("only a DRAFT preset version can be activated")
    for v in _versions(db, preset.preset_id):
        if v.status == enums.PresetVersionStatus.ACTIVE.value:
            v.status = enums.PresetVersionStatus.SUPERSEDED.value
    version.status = enums.PresetVersionStatus.ACTIVE.value
    AuditService(db).record(
        action="preset.version_activated",
        actor_user_id=actor.user_id,
        target_type=_PRES,
        target_id=str(preset.preset_id),
        meta={"version": version_no},
    )
    return version


def _set_default(db: Session, preset: Preset) -> None:
    for other in db.scalars(
        select(Preset).where(
            Preset.business_type_key == preset.business_type_key,
            Preset.is_default.is_(True),
        )
    ).all():
        other.is_default = False
    preset.is_default = True


def set_default(db: Session, *, actor: User, preset_id: uuid.UUID) -> Preset:
    preset = get_preset(db, preset_id)
    if active_version(db, preset) is None:
        raise ConflictError("a preset needs an ACTIVE version before becoming default")
    _set_default(db, preset)
    AuditService(db).record(
        action="preset.default_set",
        actor_user_id=actor.user_id,
        target_type=_PRES,
        target_id=str(preset.preset_id),
        meta={"business_type": preset.business_type_key},
    )
    return preset


def list_business_type_presets(db: Session, *, business_type_key: str) -> list[dict]:
    """What a business can see: presets of its own business type."""
    out: list[dict] = []
    for preset in db.scalars(
        select(Preset).where(Preset.business_type_key == business_type_key)
    ).all():
        av = active_version(db, preset)
        out.append(
            {
                "preset_id": preset.preset_id,
                "name": preset.name,
                "description": preset.description,
                "is_default": preset.is_default,
                "active_version": av.version if av else None,
            }
        )
    return out


def resolve_default_preset(
    db: Session, *, business_type_key: str
) -> tuple[Preset, PresetVersion] | None:
    """The default ACTIVE preset for a business type (renderer entry)."""
    preset = db.scalars(
        select(Preset).where(
            Preset.business_type_key == business_type_key,
            Preset.is_default.is_(True),
        )
    ).first()
    if preset is None:
        return None
    av = active_version(db, preset)
    if av is None:
        return None
    return preset, av


def render_preset_blocks(
    db: Session, *, preset: Preset, version_no: int | None, ctx: RenderContext
):
    """Render helper shared by the preview service."""
    av = active_version(db, preset)
    version = av if version_no is None else get_preset_version(
        db, preset.preset_id, version_no
    )
    blocks = [ContentBlock.from_dict(b) for b in version.blocks]
    return render_blocks(blocks, ctx)
