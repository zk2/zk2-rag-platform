"""Auth + access-request public endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from zk2.auth.invites import accept_invite, create_access_request
from zk2.auth.models import Membership, Organization, User
from zk2.auth.schemas import (
    AccessRequestCreate,
    GenericMessage,
    InviteAcceptRequest,
    LoginRequest,
    MagicLinkRequest,
    MagicLinkVerifyRequest,
    MeResponse,
    MembershipDto,
    RefreshRequest,
    TokenResponse,
    UserDto,
)
from zk2.auth.service import (
    authenticate_user,
    consume_magic_link,
    refresh_tokens,
    revoke_session_by_token,
    send_magic_link,
)
from zk2.core.deps import (
    current_user_dep,
    get_client_ip,
    get_db_dep,
    get_redis_dep,
    get_user_agent,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    redis: Annotated[Redis, Depends(get_redis_dep)],
) -> TokenResponse:
    _, access, refresh, expires_in = await authenticate_user(
        db,
        redis,
        email=payload.email.lower(),
        password=payload.password,
        ip=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
    return TokenResponse(access_token=access, refresh_token=refresh, expires_in=expires_in)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    payload: RefreshRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> TokenResponse:
    _, access, refresh_token, expires_in = await refresh_tokens(
        db,
        refresh_token=payload.refresh_token,
        ip=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
    return TokenResponse(
        access_token=access, refresh_token=refresh_token, expires_in=expires_in
    )


@router.post("/logout", response_model=GenericMessage)
async def logout(
    payload: RefreshRequest,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> GenericMessage:
    await revoke_session_by_token(db, payload.refresh_token)
    return GenericMessage(message="Logged out")


@router.post("/magic/request", response_model=GenericMessage)
async def magic_request(
    payload: MagicLinkRequest,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> GenericMessage:
    await send_magic_link(db, email=payload.email.lower())
    return GenericMessage(message="If this email is registered, a link has been sent")


@router.post("/magic/verify", response_model=TokenResponse)
async def magic_verify(
    payload: MagicLinkVerifyRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> TokenResponse:
    _, access, refresh, expires_in = await consume_magic_link(
        db,
        token=payload.token,
        ip=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
    return TokenResponse(access_token=access, refresh_token=refresh, expires_in=expires_in)


@router.post(
    "/invite/accept", response_model=TokenResponse, status_code=status.HTTP_201_CREATED
)
async def invite_accept(
    payload: InviteAcceptRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> TokenResponse:
    user, _ = await accept_invite(
        db,
        token=payload.token,
        password=payload.password,
        full_name=payload.full_name,
        ip=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
    # Issue tokens immediately so the user is logged in
    from zk2.auth.service import _issue_tokens  # local import to keep service private

    access, refresh, expires_in = await _issue_tokens(
        db, user=user, ip=get_client_ip(request), user_agent=get_user_agent(request)
    )
    return TokenResponse(access_token=access, refresh_token=refresh, expires_in=expires_in)


@router.get("/me", response_model=MeResponse)
async def me(
    user: Annotated[User, Depends(current_user_dep)],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> MeResponse:
    rows = await db.execute(
        select(Membership, Organization)
        .join(Organization, Organization.id == Membership.org_id)
        .where(Membership.user_id == user.id)
    )
    memberships = [
        MembershipDto(
            org_id=org.id, org_slug=org.slug, org_name=org.name, role=m.role
        )
        for m, org in rows.all()
    ]
    return MeResponse(user=UserDto.model_validate(user), memberships=memberships)


# ─── Public access request endpoint ─────────────────────────


public_router = APIRouter(prefix="/access-requests", tags=["access"])


@public_router.post(
    "", response_model=GenericMessage, status_code=status.HTTP_201_CREATED
)
async def submit_access_request(
    payload: AccessRequestCreate,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    redis: Annotated[Redis, Depends(get_redis_dep)],
) -> GenericMessage:
    await create_access_request(
        db,
        redis,
        email=payload.email.lower(),
        message=payload.message,
        ip=get_client_ip(request),
    )
    return GenericMessage(message="Request submitted; we will notify you by email")
