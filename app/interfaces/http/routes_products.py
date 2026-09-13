"""Phase 3 routes: sources, mapping, imports, products, media, review cases.

Permission model (RBAC matrix + Phase 3 additions, owner-approved):
- sources.view / sources.manage      -> source lifecycle + mapping versions
- products.import                    -> run imports + preview
- products.review_mapping            -> manual product edits, archive,
                                        review case list + resolve
- products.view / products.manage_media -> read products, media ops
All endpoints are business-scoped; cross-tenant access fails closed (404).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy import func, select

from app.application import import_pipeline
from app.application import mapping as mapping_use
from app.application import products as products_use
from app.application import review_cases as review_use
from app.application import sources as sources_use
from app.application.audit import get_correlation_id
from app.application.entitlements import get_entitlements, require_entitlement
from app.domain import enums
from app.domain.errors import (
    BusinessNotAccessible,
    ConflictError,
    EntitlementDenied,
    ValidationError,
)
from app.domain.permissions import (
    PRODUCTS_IMPORT,
    PRODUCTS_MANAGE_MEDIA,
    PRODUCTS_REVIEW_MAPPING,
    PRODUCTS_VIEW,
    SOURCES_MANAGE,
    SOURCES_VIEW,
)
from app.infrastructure.db import models
from app.interfaces.http import schemas
from app.interfaces.http.deps import CurrentUser, Db, require_business_permission

router = APIRouter(prefix="/api/v1/businesses/{business_id}", tags=["products"])

_SourcesView = Annotated[object, Depends(require_business_permission(SOURCES_VIEW))]
_SourcesManage = Annotated[object, Depends(require_business_permission(SOURCES_MANAGE))]
_ProductsImport = Annotated[object, Depends(require_business_permission(PRODUCTS_IMPORT))]
_ProductsReview = Annotated[object, Depends(require_business_permission(PRODUCTS_REVIEW_MAPPING))]
_ProductsView = Annotated[object, Depends(require_business_permission(PRODUCTS_VIEW))]
_ProductsMedia = Annotated[object, Depends(require_business_permission(PRODUCTS_MANAGE_MEDIA))]


def _require_source(db, business_id: uuid.UUID, source_id: uuid.UUID) -> models.Source:
    source = sources_use.get_source(db, business_id, source_id)
    if source is None:
        raise BusinessNotAccessible()
    return source


def _require_product(db, business_id: uuid.UUID, product_id: uuid.UUID) -> models.Product:
    product = products_use.get_product(db, business_id, product_id)
    if product is None:
        raise BusinessNotAccessible()
    return product


def _no_running_import(db, source: models.Source) -> None:
    running = db.scalar(
        select(models.ImportRun).where(
            models.ImportRun.source_id == source.source_id,
            models.ImportRun.status == enums.ImportRunStatus.RUNNING.value,
        )
    )
    if running is not None:
        raise ConflictError("an import run is already in progress for this source")


def _enforce_sync_frequency(db, business_id: uuid.UUID) -> None:
    """Plan limit: at most sync_frequency_per_day import runs per business."""
    ent = get_entitlements(db, business_id=business_id)
    if not ent.has_active or ent.sync_frequency_per_day is None:
        return
    today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    count = int(
        db.scalar(
            select(func.count())
            .select_from(models.ImportRun)
            .where(
                models.ImportRun.business_id == business_id,
                models.ImportRun.started_at >= today_start,
            )
        )
        or 0
    )
    if count >= ent.sync_frequency_per_day:
        raise EntitlementDenied(f"daily sync limit reached ({ent.sync_frequency_per_day} per day)")


# --- Sources -------------------------------------------------------------------


@router.post("/sources", response_model=schemas.SourceOut, status_code=201)
def create_source(
    business_id: uuid.UUID,
    payload: schemas.SourceCreateRequest,
    user: CurrentUser,
    db: Db,
    _scoped: _SourcesManage,
) -> schemas.SourceOut:
    require_entitlement(db, business_id=business_id, limit_key="sources")
    try:
        source = sources_use.create_source(
            db,
            business_id=business_id,
            actor_id=user.user_id,
            correlation_id=get_correlation_id(),
            name=payload.name,
            kind=enums.SourceKind(payload.kind),
            external_ref=payload.external_ref,
            sheet_name=payload.sheet_name,
            range_spec=payload.range_spec,
            credentials_ref=payload.credentials_ref,
            media_authoritative=payload.media_authoritative,
        )
    except PermissionError as exc:
        raise EntitlementDenied(str(exc)) from exc
    return schemas.SourceOut.model_validate(source)


@router.get("/sources", response_model=list[schemas.SourceOut])
def list_sources(
    business_id: uuid.UUID, db: Db, _scoped: _SourcesView
) -> list[schemas.SourceOut]:
    return [schemas.SourceOut.model_validate(s) for s in sources_use.list_sources(db, business_id)]


@router.get("/sources/{source_id}", response_model=schemas.SourceOut)
def get_source(
    business_id: uuid.UUID, source_id: uuid.UUID, db: Db, _scoped: _SourcesView
) -> schemas.SourceOut:
    source = _require_source(db, business_id, source_id)
    return schemas.SourceOut.model_validate(source)


@router.post("/sources/{source_id}/pause", response_model=schemas.SourceOut)
def pause_source(
    business_id: uuid.UUID, source_id: uuid.UUID, user: CurrentUser, db: Db, _scoped: _SourcesManage
) -> schemas.SourceOut:
    source = _require_source(db, business_id, source_id)
    source = sources_use.set_source_status(
        db,
        business_id=business_id,
        source=source,
        actor_id=user.user_id,
        correlation_id=get_correlation_id(),
        new_status=enums.SourceStatus.PAUSED,
    )
    return schemas.SourceOut.model_validate(source)


@router.post("/sources/{source_id}/resume", response_model=schemas.SourceOut)
def resume_source(
    business_id: uuid.UUID, source_id: uuid.UUID, user: CurrentUser, db: Db, _scoped: _SourcesManage
) -> schemas.SourceOut:
    source = _require_source(db, business_id, source_id)
    new_status = (
        enums.SourceStatus.ACTIVE
        if mapping_use.active_mapping_for(db, source) is not None
        else enums.SourceStatus.PENDING_MAPPING
    )
    source = sources_use.set_source_status(
        db,
        business_id=business_id,
        source=source,
        actor_id=user.user_id,
        correlation_id=get_correlation_id(),
        new_status=new_status,
    )
    return schemas.SourceOut.model_validate(source)


# --- Mapping ---------------------------------------------------------------------


@router.post("/sources/{source_id}/mapping/suggest", status_code=200)
async def suggest_mapping(
    business_id: uuid.UUID,
    source_id: uuid.UUID,
    db: Db,
    _scoped: _SourcesManage,
    file: Annotated[UploadFile | None, File()] = None,
) -> dict:
    """Read the source headers (uploaded file for Excel) and return
    heuristic suggestions. Nothing is stored — the owner reviews and
    proposes a version explicitly."""
    source = _require_source(db, business_id, source_id)
    file_bytes = await file.read() if file is not None else None
    from app.infrastructure.sources import read_source

    read = read_source(source, file_bytes=file_bytes)
    if not read.complete or read.error:
        raise ValidationError(read.error or "unreadable source")
    suggested = mapping_use.suggest_entries(read.headers)
    return {"headers": read.headers, "suggestions": mapping_use.entries_to_records(suggested)}


@router.post("/sources/{source_id}/mapping", response_model=schemas.MappingOut, status_code=201)
def propose_mapping(
    business_id: uuid.UUID,
    source_id: uuid.UUID,
    payload: schemas.MappingProposalRequest,
    user: CurrentUser,
    db: Db,
    _scoped: _SourcesManage,
) -> schemas.MappingOut:
    source = _require_source(db, business_id, source_id)
    if not any(
        e.canonical_field == "name" and e.field_kind == "CORE" for e in payload.entries
    ):
        raise ValidationError("at least the 'name' column must be mapped to a CORE field")
    mapping = mapping_use.create_mapping_version(
        db,
        source=source,
        entries=[e.model_dump() for e in payload.entries],
        actor_id=user.user_id,
    )
    return schemas.MappingOut.model_validate(mapping)


@router.get("/sources/{source_id}/mappings", response_model=list[schemas.MappingOut])
def list_mappings(
    business_id: uuid.UUID, source_id: uuid.UUID, db: Db, _scoped: _SourcesView
) -> list[schemas.MappingOut]:
    source = _require_source(db, business_id, source_id)
    return [schemas.MappingOut.model_validate(m) for m in sources_use.list_mappings(db, source)]


@router.post(
    "/sources/{source_id}/mappings/{mapping_id}/activate",
    response_model=schemas.MappingOut,
)
def activate_mapping(
    business_id: uuid.UUID,
    source_id: uuid.UUID,
    mapping_id: uuid.UUID,
    user: CurrentUser,
    db: Db,
    _scoped: _SourcesManage,
) -> schemas.MappingOut:
    source = _require_source(db, business_id, source_id)
    mapping = sources_use.get_mapping(db, source, mapping_id)
    if mapping is None:
        raise BusinessNotAccessible()
    if mapping.status == enums.MappingStatus.SUPERSEDED.value:
        raise ConflictError("a superseded mapping cannot be activated")
    mapping = mapping_use.activate_mapping(
        db, source=source, mapping=mapping, actor_id=user.user_id
    )
    return schemas.MappingOut.model_validate(mapping)


# --- Imports -----------------------------------------------------------------------


@router.post("/sources/{source_id}/imports/preview", status_code=200)
async def preview_import(
    business_id: uuid.UUID,
    source_id: uuid.UUID,
    db: Db,
    _scoped: _ProductsImport,
    file: Annotated[UploadFile | None, File()] = None,
) -> dict:
    """Dry run: predicted per-row outcomes with zero writes."""
    source = _require_source(db, business_id, source_id)
    file_bytes = await file.read() if file is not None else None
    return import_pipeline.preview_import(
        db, source=source, business_id=business_id, file_bytes=file_bytes
    )


@router.post("/sources/{source_id}/imports", response_model=schemas.ImportRunOut, status_code=202)
async def run_import_endpoint(
    business_id: uuid.UUID,
    source_id: uuid.UUID,
    user: CurrentUser,
    db: Db,
    _scoped: _ProductsImport,
    file: Annotated[UploadFile | None, File()] = None,
) -> schemas.ImportRunOut:
    source = _require_source(db, business_id, source_id)
    if source.status == enums.SourceStatus.PAUSED.value:
        raise ConflictError("source is paused — resume it before importing")
    _no_running_import(db, source)
    _enforce_sync_frequency(db, business_id)
    file_bytes = await file.read() if file is not None else None
    try:
        outcome = import_pipeline.run_import(
            db,
            source=source,
            business_id=business_id,
            actor_id=user.user_id,
            correlation_id=get_correlation_id(),
            file_bytes=file_bytes,
        )
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    return schemas.ImportRunOut.model_validate(outcome.run)


@router.get("/sources/{source_id}/imports", response_model=list[schemas.ImportRunOut])
def list_import_runs(
    business_id: uuid.UUID, source_id: uuid.UUID, db: Db, _scoped: _SourcesView
) -> list[schemas.ImportRunOut]:
    _source = _require_source(db, business_id, source_id)
    return [
        schemas.ImportRunOut.model_validate(r)
        for r in sources_use.list_import_runs(db, business_id, source_id)
    ]


# --- Products ------------------------------------------------------------------------


@router.get("/products", response_model=list[schemas.ProductOut])
def list_products(
    business_id: uuid.UUID,
    db: Db,
    _scoped: _ProductsView,
    state: Annotated[str | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=120)] = None,
) -> list[schemas.ProductOut]:
    parsed_state = enums.ProductLifecycle(state) if state else None
    try:
        items = products_use.list_products(db, business_id, state=parsed_state, q=q)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    return [schemas.ProductOut.model_validate(p) for p in items]


@router.get("/products/{product_id}", response_model=schemas.ProductOut)
def get_product(
    business_id: uuid.UUID, product_id: uuid.UUID, db: Db, _scoped: _ProductsView
) -> schemas.ProductOut:
    product = _require_product(db, business_id, product_id)
    return schemas.ProductOut.model_validate(product)


@router.patch("/products/{product_id}", response_model=schemas.ProductOut)
def update_product(
    business_id: uuid.UUID,
    product_id: uuid.UUID,
    payload: schemas.ProductUpdateRequest,
    user: CurrentUser,
    db: Db,
    _scoped: _ProductsReview,
) -> schemas.ProductOut:
    try:
        product, _version = products_use.update_product(
            db,
            business_id=business_id,
            product_id=product_id,
            actor_id=user.user_id,
            correlation_id=get_correlation_id(),
            fields=payload.model_dump(exclude_none=True),
        )
    except LookupError:
        raise BusinessNotAccessible() from None
    return schemas.ProductOut.model_validate(product)


@router.get("/products/{product_id}/versions", response_model=list[schemas.ProductVersionOut])
def list_product_versions(
    business_id: uuid.UUID, product_id: uuid.UUID, db: Db, _scoped: _ProductsView
) -> list[schemas.ProductVersionOut]:
    product = _require_product(db, business_id, product_id)
    versions = db.scalars(
        select(models.ProductVersion)
        .where(models.ProductVersion.product_id == product.product_id)
        .order_by(models.ProductVersion.version_no.desc())
        .limit(100)
    )
    return [schemas.ProductVersionOut.model_validate(v) for v in versions]


# --- Media ---------------------------------------------------------------------------


@router.get("/products/{product_id}/media", response_model=list[schemas.ProductMediaOut])
def list_product_media(
    business_id: uuid.UUID, product_id: uuid.UUID, db: Db, _scoped: _ProductsView
) -> list[schemas.ProductMediaOut]:
    product = _require_product(db, business_id, product_id)
    media = products_use.list_media(db, product.product_id)
    return [schemas.ProductMediaOut.model_validate(m) for m in media]


@router.post(
    "/products/{product_id}/media", response_model=schemas.ProductMediaOut, status_code=201
)
def add_product_media(
    business_id: uuid.UUID,
    product_id: uuid.UUID,
    payload: schemas.ProductMediaIn,
    user: CurrentUser,
    db: Db,
    _scoped: _ProductsMedia,
) -> schemas.ProductMediaOut:
    try:
        media = products_use.add_media(
            db,
            business_id=business_id,
            product_id=product_id,
            actor_id=user.user_id,
            correlation_id=get_correlation_id(),
            url=payload.url,
            origin=enums.MediaOrigin(payload.origin),
        )
    except LookupError:
        raise BusinessNotAccessible() from None
    return schemas.ProductMediaOut.model_validate(media)


@router.delete("/products/{product_id}/media/{media_id}", status_code=204)
def remove_product_media(
    business_id: uuid.UUID,
    product_id: uuid.UUID,
    media_id: uuid.UUID,
    user: CurrentUser,
    db: Db,
    _scoped: _ProductsMedia,
) -> None:
    try:
        products_use.remove_media(
            db,
            business_id=business_id,
            product_id=product_id,
            media_id=media_id,
            actor_id=user.user_id,
            correlation_id=get_correlation_id(),
        )
    except LookupError:
        raise BusinessNotAccessible() from None


# --- Lifecycle -------------------------------------------------------------------------


@router.post("/products/{product_id}/archive", response_model=schemas.ProductOut)
def archive_product(
    business_id: uuid.UUID,
    product_id: uuid.UUID,
    user: CurrentUser,
    db: Db,
    _scoped: _ProductsReview,
) -> schemas.ProductOut:
    try:
        product = products_use.archive_product(
            db,
            business_id=business_id,
            product_id=product_id,
            actor_id=user.user_id,
            correlation_id=get_correlation_id(),
        )
    except LookupError:
        raise BusinessNotAccessible() from None
    except ValueError as exc:
        raise ConflictError(str(exc)) from None
    return schemas.ProductOut.model_validate(product)


@router.post("/products/{product_id}/restore", response_model=schemas.ProductOut)
def restore_product(
    business_id: uuid.UUID,
    product_id: uuid.UUID,
    user: CurrentUser,
    db: Db,
    _scoped: _ProductsReview,
) -> schemas.ProductOut:
    try:
        product = products_use.restore_product(
            db,
            business_id=business_id,
            product_id=product_id,
            actor_id=user.user_id,
            correlation_id=get_correlation_id(),
        )
    except LookupError:
        raise BusinessNotAccessible() from None
    except ValueError as exc:
        raise ConflictError(str(exc)) from None
    return schemas.ProductOut.model_validate(product)


# --- Review cases ----------------------------------------------------------------------


@router.get("/review-cases", response_model=list[schemas.ReviewCaseOut])
def list_review_cases(
    business_id: uuid.UUID,
    db: Db,
    _scoped: _ProductsReview,
    status: Annotated[str | None, Query()] = None,
    kind: Annotated[str | None, Query()] = None,
) -> list[schemas.ReviewCaseOut]:
    parsed_status = enums.ReviewCaseStatus(status) if status else None
    parsed_kind = enums.ReviewCaseKind(kind) if kind else None
    try:
        cases = review_use.list_cases(db, business_id, status=parsed_status, kind=parsed_kind)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    return [schemas.ReviewCaseOut.model_validate(c) for c in cases]


@router.post("/review-cases/{case_id}/resolve", response_model=schemas.ReviewCaseOut)
def resolve_review_case(
    business_id: uuid.UUID,
    case_id: uuid.UUID,
    payload: schemas.ReviewCaseResolveRequest,
    user: CurrentUser,
    db: Db,
    _scoped: _ProductsReview,
) -> schemas.ReviewCaseOut:
    try:
        case = review_use.resolve_case(
            db,
            business_id=business_id,
            case_id=case_id,
            actor_id=user.user_id,
            correlation_id=get_correlation_id(),
            action=payload.action,
            target_product_id=payload.target_product_id,
        )
    except LookupError:
        raise BusinessNotAccessible() from None
    except ValueError as exc:
        raise ConflictError(str(exc)) from None
    return schemas.ReviewCaseOut.model_validate(case)
