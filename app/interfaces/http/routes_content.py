"""Phase 4 routes: content presets, per-product presets, AI definitions,
AI generation/approval, preview.

Permission model (RBAC matrix, Phase 4 additions owner-approved):
- platform operator (super admin): business-type preset CRUD + AI output
  definition registry (``admin_router``);
- business members:
  - products.view -> view presets of own business type, preview
  - product_presets.manage -> per-product preset CRUD/assign (top-plan
    entitlement enforced in the service layer)
  - ai.view / ai.generate / ai.retry / ai.edit_output /
    ai.approve_output / ai.toggle_automatic

All business endpoints are tenant-scoped; cross-business access fails
closed (404).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.application import ai_definitions, ai_generation, content_preview
from app.application import presets as presets_use
from app.application import product_presets as pp_use
from app.domain.errors import BusinessNotAccessible
from app.domain.permissions import (
    AI_APPROVE_OUTPUT,
    AI_EDIT_OUTPUT,
    AI_GENERATE,
    AI_RETRY,
    AI_TOGGLE_AUTOMATIC,
    AI_VIEW,
    PRODUCT_PRESETS_MANAGE,
    PRODUCTS_VIEW,
)
from app.interfaces.http import schemas
from app.interfaces.http.deps import (
    CurrentUser,
    Db,
    SuperAdmin,
    require_business_permission,
)

# --- Platform operator (business-type presets + AI definitions) ---
admin_router = APIRouter(prefix="/api/v1/admin", tags=["content-admin"])


@admin_router.get("/presets", response_model=list[schemas.PresetDetailOut])
def admin_list_presets(db: Db, _operator: SuperAdmin):
    out = []
    for preset in presets_use.list_presets(db):
        av = presets_use.active_version(db, preset)
        data = {c.name: getattr(preset, c.name) for c in preset.__table__.columns}
        out.append(
            schemas.PresetDetailOut(
                **data,
                versions=[
                    schemas.PresetVersionOut.model_validate(v)
                    for v in presets_use._versions(db, preset.preset_id)
                ],
                active_version=av.version if av else None,
            )
        )
    return out


@admin_router.post("/presets", response_model=schemas.PresetOut, status_code=201)
def admin_create_preset(
    db: Db, operator: SuperAdmin, body: schemas.PresetCreateRequest
):
    return presets_use.create_preset(
        db,
        actor=operator,
        business_type_key=body.business_type_key,
        name=body.name,
        description=body.description,
        is_default=body.is_default,
        blocks=[b.model_dump() for b in body.blocks],
    )


@admin_router.post("/presets/{preset_id}/versions", status_code=201)
def admin_propose_preset_version(
    db: Db,
    operator: SuperAdmin,
    preset_id: uuid.UUID,
    body: schemas.PresetVersionRequest,
):
    version = presets_use.propose_version(
        db,
        actor=operator,
        preset_id=preset_id,
        blocks=[b.model_dump() for b in body.blocks],
    )
    return JSONResponse(
        status_code=201,
        content=schemas.PresetVersionOut.model_validate(version).model_dump(
            mode="json"
        ),
    )


@admin_router.post("/presets/{preset_id}/versions/{version_no}/activate")
def admin_activate_preset_version(
    db: Db, operator: SuperAdmin, preset_id: uuid.UUID, version_no: int
):
    version = presets_use.activate_version(
        db, actor=operator, preset_id=preset_id, version_no=version_no
    )
    return schemas.PresetVersionOut.model_validate(version)


@admin_router.post("/presets/{preset_id}/default")
def admin_set_default_preset(db: Db, operator: SuperAdmin, preset_id: uuid.UUID):
    return presets_use.set_default(db, actor=operator, preset_id=preset_id)


@admin_router.get("/ai-definitions", response_model=list[schemas.AIDefinitionOut])
def admin_list_ai_definitions(db: Db, _operator: SuperAdmin):
    return ai_definitions.list_definitions(db)


@admin_router.post(
    "/ai-definitions", response_model=schemas.AIDefinitionOut, status_code=201
)
def admin_create_ai_definition(
    db: Db, operator: SuperAdmin, body: schemas.AIDefinitionCreateRequest
):
    return ai_definitions.create_definition(
        db,
        actor=operator,
        key=body.key,
        display_name=body.display_name,
        prompt_template=body.prompt_template,
        input_fields=body.input_fields,
        max_output_length=body.max_output_length,
        cost_credits=body.cost_credits,
        active=body.active,
    )


@admin_router.post("/ai-definitions/{key}/versions", status_code=201)
def admin_create_ai_definition_version(
    db: Db, operator: SuperAdmin, key: str, body: schemas.AIDefinitionVersionRequest
):
    row = ai_definitions.create_definition_version(
        db,
        actor=operator,
        key=key,
        prompt_template=body.prompt_template,
        input_fields=body.input_fields,
        max_output_length=body.max_output_length,
        cost_credits=body.cost_credits,
        activate=body.activate,
    )
    return JSONResponse(
        status_code=201,
        content=schemas.AIDefinitionOut.model_validate(row).model_dump(mode="json"),
    )


# --- Business-scoped content ---
router = APIRouter(prefix="/api/v1/businesses/{business_id}", tags=["content"])

_ProductsView = Annotated[object, Depends(require_business_permission(PRODUCTS_VIEW))]
_PresetsManage = Annotated[
    object, Depends(require_business_permission(PRODUCT_PRESETS_MANAGE))
]
_AIView = Annotated[object, Depends(require_business_permission(AI_VIEW))]
_AIGenerate = Annotated[object, Depends(require_business_permission(AI_GENERATE))]
_AIRetry = Annotated[object, Depends(require_business_permission(AI_RETRY))]
_AIEdit = Annotated[object, Depends(require_business_permission(AI_EDIT_OUTPUT))]
_AIApprove = Annotated[object, Depends(require_business_permission(AI_APPROVE_OUTPUT))]
_AIToggle = Annotated[
    object, Depends(require_business_permission(AI_TOGGLE_AUTOMATIC))
]


@router.get("/presets", response_model=list[schemas.BusinessTypePresetOut])
def list_presets(db: Db, _scope: _ProductsView):
    business = _scope[0]
    return presets_use.list_business_type_presets(
        db, business_type_key=business.business_type_key
    )


@router.get("/presets/{preset_id}/versions/{version_no}")
def get_preset_version(
    db: Db, _scope: _ProductsView, preset_id: uuid.UUID, version_no: int
):
    business = _scope[0]
    preset = presets_use.get_preset(db, preset_id)
    if preset.business_type_key != business.business_type_key:
        raise BusinessNotAccessible()
    version = presets_use.get_preset_version(db, preset_id, version_no)
    return schemas.PresetVersionOut.model_validate(version)


# --- Per-product presets (top-plan entitlement in the service layer) ---


@router.get("/product-presets", response_model=list[schemas.ProductPresetOut])
def list_product_presets(db: Db, _scope: _ProductsView):
    business = _scope[0]
    return pp_use.list_product_presets(db, business_id=business.business_id)


@router.post(
    "/product-presets", response_model=schemas.ProductPresetOut, status_code=201
)
def create_product_preset(
    db: Db,
    user: CurrentUser,
    _scope: _PresetsManage,
    body: schemas.ProductPresetCreateRequest,
):
    business = _scope[0]
    return pp_use.create_product_preset(
        db,
        business=business,
        actor=user,
        name=body.name,
        description=body.description,
        blocks=[b.model_dump() for b in body.blocks],
    )


@router.post("/product-presets/{product_preset_id}/versions", status_code=201)
def propose_product_preset_version(
    db: Db,
    user: CurrentUser,
    _scope: _PresetsManage,
    product_preset_id: uuid.UUID,
    body: schemas.PresetVersionRequest,
):
    business = _scope[0]
    version = pp_use.propose_product_preset_version(
        db,
        business=business,
        actor=user,
        product_preset_id=product_preset_id,
        blocks=[b.model_dump() for b in body.blocks],
    )
    return JSONResponse(
        status_code=201,
        content=schemas.ProductPresetVersionOut.model_validate(version).model_dump(
            mode="json"
        ),
    )


@router.post(
    "/product-presets/{product_preset_id}/versions/{version_no}/activate"
)
def activate_product_preset_version(
    db: Db,
    user: CurrentUser,
    _scope: _PresetsManage,
    product_preset_id: uuid.UUID,
    version_no: int,
):
    business = _scope[0]
    version = pp_use.activate_product_preset_version(
        db,
        business=business,
        actor=user,
        product_preset_id=product_preset_id,
        version_no=version_no,
    )
    return schemas.ProductPresetVersionOut.model_validate(version)


@router.post("/products/{product_id}/preset")
def assign_product_preset(
    db: Db,
    user: CurrentUser,
    _scope: _PresetsManage,
    product_id: uuid.UUID,
    body: schemas.ProductPresetAssignRequest,
):
    business = _scope[0]
    product = pp_use.assign_product_preset(
        db,
        business=business,
        actor=user,
        product_id=product_id,
        product_preset_id=body.product_preset_id,
    )
    return {
        "product_id": product.product_id,
        "product_preset_id": product.product_preset_id,
    }


# --- AI (business) ---


@router.get("/products/{product_id}/ai", response_model=list[schemas.AIArtifactOut])
def list_ai_artifacts(db: Db, _scope: _AIView, product_id: uuid.UUID):
    business = _scope[0]
    return ai_generation.list_artifacts(db, business=business, product_id=product_id)


def _generate_result(outcome: dict) -> schemas.AIGenerateResult:
    return schemas.AIGenerateResult(
        artifact=schemas.AIArtifactOut.model_validate(outcome["artifact"]),
        reused=outcome["reused"],
        definition_key=outcome["definition"].key,
        definition_version=outcome["definition"].version,
    )


@router.post(
    "/products/{product_id}/ai/generate", response_model=schemas.AIGenerateResult
)
def generate_ai(
    db: Db,
    user: CurrentUser,
    _scope: _AIGenerate,
    product_id: uuid.UUID,
    body: schemas.AIGenerateRequest,
):
    business = _scope[0]
    outcome = ai_generation.generate(
        db,
        business=business,
        actor=user,
        product_id=product_id,
        definition_key=body.definition_key,
    )
    return _generate_result(outcome)


@router.post("/products/{product_id}/ai/retry", response_model=schemas.AIGenerateResult)
def retry_ai(
    db: Db,
    user: CurrentUser,
    _scope: _AIRetry,
    product_id: uuid.UUID,
    body: schemas.AIGenerateRequest,
):
    business = _scope[0]
    outcome = ai_generation.generate(
        db,
        business=business,
        actor=user,
        product_id=product_id,
        definition_key=body.definition_key,
        is_retry=True,
    )
    return _generate_result(outcome)


@router.post("/ai/artifacts/{artifact_id}", response_model=schemas.AIArtifactOut)
def edit_ai_artifact(
    db: Db,
    user: CurrentUser,
    _scope: _AIEdit,
    artifact_id: uuid.UUID,
    body: schemas.AIArtifactEditRequest,
):
    business = _scope[0]
    return ai_generation.edit_artifact(
        db,
        business=business,
        actor=user,
        artifact_id=artifact_id,
        text=body.text,
    )


@router.post("/ai/artifacts/{artifact_id}/approve", response_model=schemas.AIArtifactOut)
def approve_ai_artifact(db: Db, user: CurrentUser, _scope: _AIApprove, artifact_id: uuid.UUID):
    business = _scope[0]
    return ai_generation.approve_artifact(
        db, business=business, actor=user, artifact_id=artifact_id
    )


@router.post("/ai/artifacts/{artifact_id}/reject", response_model=schemas.AIArtifactOut)
def reject_ai_artifact(db: Db, user: CurrentUser, _scope: _AIApprove, artifact_id: uuid.UUID):
    business = _scope[0]
    return ai_generation.reject_artifact(
        db, business=business, actor=user, artifact_id=artifact_id
    )


@router.post("/ai/automatic")
def toggle_ai_automatic(
    db: Db, user: CurrentUser, _scope: _AIToggle, body: schemas.AIToggleAutomaticRequest
):
    business = _scope[0]
    business = ai_generation.set_automatic(
        db, business=business, actor=user, enabled=body.enabled
    )
    return {
        "business_id": business.business_id,
        "ai_automatic_enabled": business.ai_automatic_enabled,
    }


@router.get("/products/{product_id}/preview", response_model=schemas.PreviewOut)
def preview_product(
    db: Db, _scope: _ProductsView, product_id: uuid.UUID, version: int | None = None
):
    business = _scope[0]
    return content_preview.preview_product(
        db, business=business, product_id=product_id, preset_version=version
    )
