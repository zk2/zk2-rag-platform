"""Sources REST API."""

from __future__ import annotations

from typing import Annotated

from arq import ArqRedis
from fastapi import APIRouter, Depends, File, Query, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from zk2.auth.rbac import OrgContext, require_org
from zk2.core.arq import get_arq_dep
from zk2.core.deps import get_db_dep
from zk2.core.errors import NotFoundError
from zk2.sources.models import Source, SourceChunk
from zk2.sources.schemas import (
    ChunkDto,
    DirectoryCreate,
    SitemapImport,
    SourceDto,
    SourceNodeDto,
    UrlCreate,
)
from zk2.sources.service import (
    create_directory,
    create_file_source,
    create_url_source,
    delete_source,
    get_tree,
    import_sitemap,
)

router = APIRouter(prefix="/sources", tags=["sources"])


@router.post("/directory", response_model=SourceDto, status_code=status.HTTP_201_CREATED)
async def create_directory_endpoint(
    payload: DirectoryCreate,
    ctx: Annotated[OrgContext, Depends(require_org("editor"))],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> SourceDto:
    row = await create_directory(
        db, org_id=ctx.org_id, name=payload.name, parent_id=payload.parent_id
    )
    return SourceDto.model_validate(row)


@router.post("/file", response_model=SourceDto, status_code=status.HTTP_201_CREATED)
async def upload_file_endpoint(
    ctx: Annotated[OrgContext, Depends(require_org("editor"))],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    arq: Annotated[ArqRedis, Depends(get_arq_dep)],
    file: Annotated[UploadFile, File()],
    parent_id: Annotated[int | None, Query()] = None,
) -> SourceDto:
    content = await file.read()
    row = await create_file_source(
        db,
        arq,
        org_id=ctx.org_id,
        parent_id=parent_id,
        filename=file.filename or "unnamed",
        content=content,
    )
    return SourceDto.model_validate(row)


@router.post("/url", response_model=SourceDto, status_code=status.HTTP_201_CREATED)
async def create_url_endpoint(
    payload: UrlCreate,
    ctx: Annotated[OrgContext, Depends(require_org("editor"))],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    arq: Annotated[ArqRedis, Depends(get_arq_dep)],
) -> SourceDto:
    row = await create_url_source(
        db, arq, org_id=ctx.org_id, parent_id=payload.parent_id, url=str(payload.url)
    )
    return SourceDto.model_validate(row)


@router.post("/sitemap", response_model=list[SourceDto], status_code=status.HTTP_202_ACCEPTED)
async def import_sitemap_endpoint(
    payload: SitemapImport,
    ctx: Annotated[OrgContext, Depends(require_org("editor"))],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    arq: Annotated[ArqRedis, Depends(get_arq_dep)],
) -> list[SourceDto]:
    rows = await import_sitemap(
        db,
        arq,
        org_id=ctx.org_id,
        parent_id=payload.parent_id,
        base_url=str(payload.base_url),
        limit=payload.limit,
    )
    return [SourceDto.model_validate(r) for r in rows]


@router.get("/tree", response_model=list[SourceNodeDto])
async def get_tree_endpoint(
    ctx: Annotated[OrgContext, Depends(require_org("viewer"))],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    parent_id: Annotated[int | None, Query()] = None,
    directories_only: Annotated[bool, Query()] = False,
) -> list[SourceNodeDto]:
    tree = await get_tree(
        db, org_id=ctx.org_id, parent_id=parent_id, directories_only=directories_only
    )
    return [SourceNodeDto.model_validate(n) for n in tree]


@router.get("/{source_id}", response_model=SourceDto)
async def get_source_endpoint(
    source_id: int,
    ctx: Annotated[OrgContext, Depends(require_org("viewer"))],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> SourceDto:
    row = await db.scalar(select(Source).where(Source.id == source_id, Source.org_id == ctx.org_id))
    if row is None:
        raise NotFoundError("Source not found")
    return SourceDto.model_validate(row)


@router.get("/{source_id}/chunks", response_model=list[ChunkDto])
async def list_chunks_endpoint(
    source_id: int,
    ctx: Annotated[OrgContext, Depends(require_org("viewer"))],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[ChunkDto]:
    own = await db.scalar(
        select(Source.id).where(Source.id == source_id, Source.org_id == ctx.org_id)
    )
    if own is None:
        raise NotFoundError("Source not found")
    rows = (
        (
            await db.execute(
                select(SourceChunk)
                .where(SourceChunk.source_id == source_id)
                .order_by(SourceChunk.ordinal)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [ChunkDto.model_validate(r) for r in rows]


@router.delete("/{source_id}", status_code=status.HTTP_200_OK)
async def delete_source_endpoint(
    source_id: int,
    ctx: Annotated[OrgContext, Depends(require_org("editor"))],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> dict[str, int]:
    deleted = await delete_source(db, org_id=ctx.org_id, source_id=source_id)
    return {"deleted": deleted}
