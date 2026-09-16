"""Business routes: types, create, list, members."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.application import business as business_use
from app.application import membership as membership_use
from app.domain.permissions import BUSINESS_VIEW
from app.interfaces.http import schemas
from app.interfaces.http.deps import (
    CurrentUser,
    Db,
    require_business_permission,
)

router = APIRouter(prefix="/api/v1", tags=["business"])

BusinessViewScoped = Annotated[
    object, Depends(require_business_permission(BUSINESS_VIEW))
]


@router.get("/business-types", response_model=list[schemas.BusinessTypeOut])
def list_business_types(db: Db) -> list[schemas.BusinessTypeOut]:
    return [schemas.BusinessTypeOut.model_validate(t) for t in business_use.list_business_types(db)]


@router.post("/businesses", response_model=schemas.BusinessOut, status_code=201)
def create_business(
    payload: schemas.CreateBusinessRequest, user: CurrentUser, db: Db
) -> schemas.BusinessOut:
    b = business_use.create_business(
        db, user=user, name=payload.name, business_type_key=payload.business_type_key
    )
    return schemas.BusinessOut.model_validate(b)


@router.get("/businesses", response_model=list[schemas.BusinessOut])
def list_businesses(user: CurrentUser, db: Db) -> list[schemas.BusinessOut]:
    return [
        schemas.BusinessOut.model_validate(b) for b in business_use.list_businesses(db, user=user)
    ]


@router.get("/businesses/{business_id}/members", response_model=list[schemas.MemberOut])
def list_members(
    business_id: uuid.UUID,
    user: CurrentUser,
    db: Db,
    _scoped: BusinessViewScoped,
) -> list[schemas.MemberOut]:
    rows = membership_use.list_members(db, viewer=user, business_id=business_id)
    return [schemas.MemberOut.model_validate(m) for m in rows]


@router.delete("/businesses/{business_id}", status_code=204)
def delete_business(
    business_id: uuid.UUID,
    user: CurrentUser,
    db: Db,
):
    business_use.delete_business(db, user=user, business_id=business_id)
    return None
