"""Organization settings API."""

from __future__ import annotations

from typing import Annotated

from arq import ArqRedis
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from zk2.auth.rbac import OrgContext, require_org
from zk2.core.arq import get_arq_dep
from zk2.core.audit import write_audit
from zk2.core.deps import get_db_dep
from zk2.orgs.schemas import EmbeddingSettingsDto, EmbeddingSettingsUpdate, ReindexStartedDto
from zk2.orgs.service import (
    describe_embedding_settings,
    queue_reindex,
    update_embedding_settings,
)

router = APIRouter(prefix="/settings/embedding", tags=["settings"])


@router.get("", response_model=EmbeddingSettingsDto)
async def get_embedding_settings(
    ctx: Annotated[OrgContext, Depends(require_org("viewer"))],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> EmbeddingSettingsDto:
    """Current model plus how much of the corpus was indexed with another one."""
    return await describe_embedding_settings(db, org_id=ctx.org_id)


@router.put("", response_model=EmbeddingSettingsDto)
async def put_embedding_settings(
    payload: EmbeddingSettingsUpdate,
    ctx: Annotated[OrgContext, Depends(require_org("admin"))],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> EmbeddingSettingsDto:
    """Change the embedding model.

    Existing vectors stay until a reindex runs; the response says how many
    sources are now stale so the UI can offer it rather than pretend the switch
    was free.
    """
    result = await update_embedding_settings(
        db,
        org_id=ctx.org_id,
        provider=payload.embedding_provider,
        model=payload.embedding_model,
    )
    await write_audit(
        db,
        action="org.embedding_model.changed",
        actor_user_id=ctx.user.id,
        org_id=ctx.org_id,
        target=payload.embedding_model,
        payload={"provider": payload.embedding_provider, "stale": result.stale_sources},
    )
    return result


@router.post("/reindex", response_model=ReindexStartedDto)
async def reindex_org(
    ctx: Annotated[OrgContext, Depends(require_org("admin"))],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    arq: Annotated[ArqRedis, Depends(get_arq_dep)],
    only_stale: bool = True,
) -> ReindexStartedDto:
    """Re-embed the corpus. Costs one embedding call per chunk - hence explicit."""
    queued = await queue_reindex(db, arq, org_id=ctx.org_id, only_stale=only_stale)
    await write_audit(
        db,
        action="org.reindex.started",
        actor_user_id=ctx.user.id,
        org_id=ctx.org_id,
        payload={"queued": queued, "only_stale": only_stale},
    )
    return ReindexStartedDto(
        queued=queued, reason="stale_embeddings" if only_stale else "full_reindex"
    )
