"""Bot CRUD endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from zk2.auth.rbac import OrgContext, require_org
from zk2.bots.schemas import BotCreate, BotDto, BotPatch
from zk2.bots.service import (
    create_bot,
    delete_bot,
    get_bot,
    hydrate_bot,
    list_bots,
    patch_bot,
)
from zk2.core.deps import get_db_dep

router = APIRouter(prefix="/bots", tags=["bots"])


@router.get("", response_model=list[BotDto])
async def list_bots_endpoint(
    ctx: Annotated[OrgContext, Depends(require_org("viewer"))],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> list[BotDto]:
    return await list_bots(db, org_id=ctx.org_id)


@router.post("", response_model=BotDto, status_code=status.HTTP_201_CREATED)
async def create_bot_endpoint(
    payload: BotCreate,
    ctx: Annotated[OrgContext, Depends(require_org("editor"))],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> BotDto:
    return await create_bot(db, org_id=ctx.org_id, user=ctx.user, payload=payload)


@router.get("/{bot_id}", response_model=BotDto)
async def get_bot_endpoint(
    bot_id: int,
    ctx: Annotated[OrgContext, Depends(require_org("viewer"))],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> BotDto:
    bot = await get_bot(db, org_id=ctx.org_id, bot_id=bot_id)
    return await hydrate_bot(db, bot)


@router.patch("/{bot_id}", response_model=BotDto)
async def patch_bot_endpoint(
    bot_id: int,
    payload: BotPatch,
    ctx: Annotated[OrgContext, Depends(require_org("editor"))],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> BotDto:
    return await patch_bot(db, org_id=ctx.org_id, user=ctx.user, bot_id=bot_id, payload=payload)


@router.delete("/{bot_id}")
async def delete_bot_endpoint(
    bot_id: int,
    ctx: Annotated[OrgContext, Depends(require_org("admin"))],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> dict[str, str]:
    await delete_bot(db, org_id=ctx.org_id, bot_id=bot_id)
    return {"status": "deleted"}
