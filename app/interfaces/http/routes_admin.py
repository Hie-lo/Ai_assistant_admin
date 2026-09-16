"""Admin access routes: invites, requests, approve/reject, revocation."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.application import membership as membership_use
from app.domain.permissions import ADMIN_MANAGE
from app.infrastructure.db.models import User
from app.interfaces.http import schemas
from app.interfaces.http.deps import CurrentUser, Db, require_business_permission

router = APIRouter(prefix="/api/v1", tags=["admin"])

OwnerScoped = Annotated[object, Depends(require_business_permission(ADMIN_MANAGE))]


def _request_out(request_id: object, db: Db, with_candidate: bool) -> schemas.AdminRequestOut:
    from app.infrastructure.db.models import AdminAccessRequest

    req = db.get(AdminAccessRequest, request_id)
    assert req is not None
    candidate = db.get(User, req.candidate_user_id) if with_candidate else None
    return schemas.AdminRequestOut(
        request_id=req.request_id,
        candidate_user_id=req.candidate_user_id,
        candidate_email=candidate.email if candidate else None,
        candidate_name=candidate.display_name if candidate else None,
        method=req.method,
        status=req.status,
        created_at=req.created_at,
        resolved_at=req.resolved_at,
    )


@router.post("/businesses/{business_id}/invites", response_model=schemas.InviteOut, status_code=201)
def create_invite(
    business_id: uuid.UUID,
    payload: schemas.CreateInviteRequest,
    user: CurrentUser,
    db: Db,
    _scoped: OwnerScoped,
) -> schemas.InviteOut:
    invite, code = membership_use.create_invite(
        db, owner=user, business_id=business_id, max_uses=payload.max_uses
    )
    return schemas.InviteOut(
        invite_id=invite.invite_id, code=code, expires_at=invite.expires_at
    )


@router.get(
    "/businesses/{business_id}/admin-requests", response_model=list[schemas.AdminRequestOut]
)
def list_pending_requests(
    business_id: uuid.UUID, user: CurrentUser, db: Db, _scoped: OwnerScoped
) -> list[schemas.AdminRequestOut]:
    rows = membership_use.list_pending_requests(db, owner=user, business_id=business_id)
    return [_request_out(r.request_id, db, with_candidate=True) for r in rows]


@router.post(
    "/businesses/{business_id}/admin-requests/{request_id}/approve",
    response_model=schemas.MemberOut,
)
def approve_request(
    business_id: uuid.UUID,
    request_id: uuid.UUID,
    user: CurrentUser,
    db: Db,
    _scoped: OwnerScoped,
) -> schemas.MemberOut:
    m = membership_use.approve_request(
        db, owner=user, business_id=business_id, request_id=request_id
    )
    return schemas.MemberOut.model_validate(m)


@router.post(
    "/businesses/{business_id}/admin-requests/{request_id}/reject",
    response_model=schemas.AdminRequestOut,
)
def reject_request(
    business_id: uuid.UUID,
    request_id: uuid.UUID,
    user: CurrentUser,
    db: Db,
    _scoped: OwnerScoped,
) -> schemas.AdminRequestOut:
    req = membership_use.reject_request(
        db, owner=user, business_id=business_id, request_id=request_id
    )
    return _request_out(req.request_id, db, with_candidate=False)


@router.post(
    "/businesses/{business_id}/memberships/{membership_id}/revoke",
    response_model=schemas.MemberOut,
)
def revoke_membership(
    business_id: uuid.UUID,
    membership_id: uuid.UUID,
    user: CurrentUser,
    db: Db,
    _scoped: OwnerScoped,
) -> schemas.MemberOut:
    m = membership_use.revoke_membership(
        db, owner=user, business_id=business_id, membership_id=membership_id
    )
    return schemas.MemberOut.model_validate(m)


@router.post("/admin-requests", response_model=schemas.AdminRequestAccepted, status_code=202)
def submit_admin_request(
    payload: schemas.SubmitAdminRequest, user: CurrentUser, db: Db
) -> schemas.AdminRequestAccepted:
    request, created = membership_use.submit_admin_request(
        db,
        candidate=user,
        method=payload.method,
        code=payload.code,
        platform=payload.platform,
        channel_id=payload.channel_id,
    )
    return schemas.AdminRequestAccepted(
        request_id=request.request_id,
        status=request.status,
        created=created,
    )
