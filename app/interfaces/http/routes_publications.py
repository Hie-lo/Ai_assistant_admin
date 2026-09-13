"""Phase 5 routes: platform connections + publications (Telegram V1).

Permission model (RBAC matrix):
- connections.view / connect / reconnect / disconnect -> connection
  lifecycle (connect requires the plan's channel entitlement; the shared
  bot credential is platform-level, so no per-business secret handling);
- posts.view / posts.publish / posts.update / posts.repost /
  posts.delete_remote -> publication operations.

All endpoints are business-scoped; cross-business access fails closed
(404). Publishing is MANUAL in V1 (scheduling lands in Phase 8).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.application import platform_connections as conn_use
from app.application import publications as pub_use
from app.application.entitlements import require_entitlement
from app.domain.permissions import (
    CONNECTIONS_CONNECT,
    CONNECTIONS_DISCONNECT,
    CONNECTIONS_RECONNECT,
    CONNECTIONS_VIEW,
    POSTS_DELETE_REMOTE,
    POSTS_PUBLISH,
    POSTS_REPOST,
    POSTS_UPDATE,
    POSTS_VIEW,
)
from app.interfaces.http import schemas
from app.interfaces.http.deps import (
    CurrentUser,
    Db,
    require_business_permission,
)

router = APIRouter(prefix="/api/v1/businesses/{business_id}", tags=["publications"])

_ConnView = Annotated[object, Depends(require_business_permission(CONNECTIONS_VIEW))]
_ConnConnect = Annotated[object, Depends(require_business_permission(CONNECTIONS_CONNECT))]
_ConnReconnect = Annotated[object, Depends(require_business_permission(CONNECTIONS_RECONNECT))]
_ConnDisconnect = Annotated[object, Depends(require_business_permission(CONNECTIONS_DISCONNECT))]
_PostsView = Annotated[object, Depends(require_business_permission(POSTS_VIEW))]
_PostsPublish = Annotated[object, Depends(require_business_permission(POSTS_PUBLISH))]
_PostsUpdate = Annotated[object, Depends(require_business_permission(POSTS_UPDATE))]
_PostsRepost = Annotated[object, Depends(require_business_permission(POSTS_REPOST))]
_PostsDelete = Annotated[object, Depends(require_business_permission(POSTS_DELETE_REMOTE))]


def _pub_out(db, pub) -> schemas.PublicationOut:
    from app.infrastructure.db.models import PostVersion

    version = db.get(PostVersion, pub.post_version_id)
    return schemas.PublicationOut(
        **{c.name: getattr(pub, c.name) for c in pub.__table__.columns},
        text=version.text if version else None,
        media_urls=list(version.media_urls) if version else [],
    )


# --- Connections ---


@router.get("/connections", response_model=list[schemas.PlatformConnectionOut])
def list_connections(db: Db, _scope: _ConnView):
    business = _scope[0]
    return conn_use.list_connections(db, business=business)


@router.post("/connections", response_model=schemas.PlatformConnectionOut, status_code=201)
def create_connection(
    db: Db, user: CurrentUser, _scope: _ConnConnect, body: schemas.ConnectionCreateRequest
):
    business = _scope[0]
    require_entitlement(db, business_id=business.business_id, limit_key="channels")
    conn = conn_use.create_connection(
        db,
        business=business,
        actor=user,
        platform=body.platform,
        target=body.target,
    )
    return conn


@router.post("/connections/{connection_id}/verify", response_model=schemas.PlatformConnectionOut)
def verify_connection(db: Db, user: CurrentUser, _scope: _ConnReconnect, connection_id: uuid.UUID):
    business = _scope[0]
    return conn_use.verify_connection(
        db, business=business, actor=user, connection_id=connection_id
    )


@router.post("/connections/{connection_id}/reconnect", response_model=schemas.PlatformConnectionOut)
def reconnect_connection(
    db: Db, user: CurrentUser, _scope: _ConnReconnect, connection_id: uuid.UUID
):
    business = _scope[0]
    return conn_use.reconnect_connection(
        db, business=business, actor=user, connection_id=connection_id
    )


@router.post(
    "/connections/{connection_id}/disconnect", response_model=schemas.PlatformConnectionOut
)
def disconnect_connection(
    db: Db, user: CurrentUser, _scope: _ConnDisconnect, connection_id: uuid.UUID
):
    business = _scope[0]
    return conn_use.disconnect_connection(
        db, business=business, actor=user, connection_id=connection_id
    )


# --- Publications ---


@router.get("/publications", response_model=list[schemas.PublicationOut])
def list_publications(db: Db, _scope: _PostsView, product_id: uuid.UUID | None = None):
    business = _scope[0]
    pubs = pub_use.list_publications(db, business=business, product_id=product_id)
    return [_pub_out(db, p) for p in pubs]


@router.get("/publications/{publication_id}", response_model=schemas.PublicationOut)
def get_publication(db: Db, _scope: _PostsView, publication_id: uuid.UUID):
    business = _scope[0]
    pub = pub_use.get_publication(
        db, business=business, publication_id=publication_id
    )
    return _pub_out(db, pub)


@router.post(
    "/products/{product_id}/publications", response_model=schemas.PublicationResult, status_code=201
)
def publish_product(
    db: Db,
    user: CurrentUser,
    _scope: _PostsPublish,
    product_id: uuid.UUID,
    body: schemas.PublishRequest,
):
    business = _scope[0]
    outcome = pub_use.publish(
        db,
        business=business,
        actor=user,
        product_id=product_id,
        connection_id=body.connection_id,
    )
    return schemas.PublicationResult(
        publication=_pub_out(db, outcome["publication"]),
        created=outcome["created"],
    )


@router.post("/publications/{publication_id}/update", response_model=schemas.PublicationResult)
def update_publication(db: Db, user: CurrentUser, _scope: _PostsUpdate, publication_id: uuid.UUID):
    business = _scope[0]
    outcome = pub_use.update_publication(
        db, business=business, actor=user, publication_id=publication_id
    )
    return schemas.PublicationResult(
        publication=_pub_out(db, outcome["publication"]),
        plan=outcome.get("plan"),
        updated=outcome.get("updated", True),
        new_publication=(
            _pub_out(db, outcome["new_publication"]) if outcome.get("new_publication") else None
        ),
    )


@router.post("/publications/{publication_id}/repost", response_model=schemas.PublicationResult)
def repost_publication(db: Db, user: CurrentUser, _scope: _PostsRepost, publication_id: uuid.UUID):
    business = _scope[0]
    outcome = pub_use.repost_publication(
        db, business=business, actor=user, publication_id=publication_id
    )
    return schemas.PublicationResult(
        publication=_pub_out(db, outcome["publication"]),
        plan=outcome.get("plan"),
        updated=outcome.get("updated", True),
        new_publication=(
            _pub_out(db, outcome["new_publication"]) if outcome.get("new_publication") else None
        ),
    )


@router.post("/publications/{publication_id}/delete", response_model=schemas.PublicationResult)
def delete_publication(db: Db, user: CurrentUser, _scope: _PostsDelete, publication_id: uuid.UUID):
    business = _scope[0]
    outcome = pub_use.delete_publication(
        db, business=business, actor=user, publication_id=publication_id
    )
    return schemas.PublicationResult(
        publication=_pub_out(db, outcome["publication"]),
        deleted=outcome.get("deleted"),
    )


@router.post("/publications/{publication_id}/check", response_model=schemas.PublicationResult)
def check_publication(db: Db, user: CurrentUser, _scope: _PostsView, publication_id: uuid.UUID):
    business = _scope[0]
    outcome = pub_use.check_publication(
        db, business=business, actor=user, publication_id=publication_id
    )
    return schemas.PublicationResult(
        publication=_pub_out(db, outcome["publication"]),
        finding=outcome.get("finding"),
    )
