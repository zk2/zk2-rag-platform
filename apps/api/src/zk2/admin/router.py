"""Super-admin endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from zk2.auth.invites import decide_access_request, issue_invite
from zk2.auth.models import AccessRequest, Invite, User
from zk2.auth.rbac import require_super_admin
from zk2.auth.schemas import (
    AccessRequestDecision,
    AccessRequestDto,
    GenericMessage,
    InviteCreate,
    InviteDto,
)
from zk2.core.deps import get_db_dep

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_super_admin())],
)


@router.get("/access-requests", response_model=list[AccessRequestDto])
async def list_access_requests(
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[AccessRequestDto]:
    stmt = select(AccessRequest).order_by(AccessRequest.created_at.desc()).limit(limit)
    if status_filter:
        stmt = stmt.where(AccessRequest.status == status_filter)
    rows = (await db.execute(stmt)).scalars().all()
    return [AccessRequestDto.model_validate(r) for r in rows]


@router.post("/access-requests/{request_id}/decide", response_model=GenericMessage)
async def decide_access_request_endpoint(
    request_id: int,
    payload: AccessRequestDecision,
    user: Annotated[User, Depends(require_super_admin())],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> GenericMessage:
    await decide_access_request(
        db,
        request_id=request_id,
        decided_by=user,
        approve=payload.approve,
        reason=payload.reason,
        create_org_name=payload.create_org_name,
    )
    return GenericMessage(
        message="Request approved and invite sent" if payload.approve else "Request rejected"
    )


@router.get("/invites", response_model=list[InviteDto])
async def list_invites(
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[InviteDto]:
    rows = (
        (await db.execute(select(Invite).order_by(Invite.created_at.desc()).limit(limit)))
        .scalars()
        .all()
    )
    return [InviteDto.model_validate(r) for r in rows]


@router.post("/invites", response_model=InviteDto)
async def create_invite_endpoint(
    payload: InviteCreate,
    user: Annotated[User, Depends(require_super_admin())],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> InviteDto:
    invite = await issue_invite(
        db,
        email=payload.email.lower(),
        role=payload.role,
        org_id=payload.org_id,
        create_org_name=payload.create_org_name,
        created_by=user,
    )
    return InviteDto.model_validate(invite)
