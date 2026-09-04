"""Organization settings, and the reindex they can require.

Dense retrieval filters on the embedding model that produced a vector. Changing
the model without rebuilding the index does not raise anything - it just
silently returns nothing, which is the worst way for a search feature to fail.
So the settings API reports how much of the corpus is stale, and reindexing is
an explicit, visible operation.
"""

from __future__ import annotations

import structlog
from arq import ArqRedis
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from zk2.core.errors import ValidationError
from zk2.llm.catalog import get_catalog
from zk2.orgs.models import OrgSettings
from zk2.orgs.schemas import EmbeddingSettingsDto

logger = structlog.get_logger()

_STALE_COUNTS_SQL = """
    SELECT
        count(DISTINCT s.id) FILTER (WHERE se.id IS NOT NULL)                      AS indexed,
        count(DISTINCT s.id) FILTER (WHERE se.id IS NOT NULL AND se.model <> :model) AS stale
    FROM sources s
    JOIN source_chunks sc ON sc.source_id = s.id
    LEFT JOIN source_embeddings se ON se.chunk_id = sc.id
    WHERE s.org_id = :org
"""


async def get_org_settings(db: AsyncSession, *, org_id: int) -> OrgSettings:
    """Settings row for an organization, created with defaults on first use."""
    row = await db.scalar(select(OrgSettings).where(OrgSettings.org_id == org_id))
    if row is None:
        row = OrgSettings(org_id=org_id)
        db.add(row)
        await db.flush()
    return row


async def describe_embedding_settings(db: AsyncSession, *, org_id: int) -> EmbeddingSettingsDto:
    settings = await get_org_settings(db, org_id=org_id)
    counts = (
        await db.execute(
            text(_STALE_COUNTS_SQL), {"org": org_id, "model": settings.embedding_model}
        )
    ).one()
    spec = get_catalog().embedding_model(settings.embedding_model)
    return EmbeddingSettingsDto(
        embedding_provider=settings.embedding_provider,
        embedding_model=settings.embedding_model,
        dimensions=spec.native_dimensions if spec else 0,
        indexed_sources=counts.indexed or 0,
        stale_sources=counts.stale or 0,
    )


async def update_embedding_settings(
    db: AsyncSession, *, org_id: int, provider: str, model: str
) -> EmbeddingSettingsDto:
    if get_catalog().embedding_model(model) is None:
        raise ValidationError(f"Unknown embedding model: {model}")

    settings = await get_org_settings(db, org_id=org_id)
    settings.embedding_provider = provider
    settings.embedding_model = model
    await db.flush()
    logger.info("org.embedding_model_changed", org_id=org_id, model=model)
    return await describe_embedding_settings(db, org_id=org_id)


async def queue_reindex(
    db: AsyncSession, arq: ArqRedis | None, *, org_id: int, only_stale: bool = False
) -> int:
    """Mark sources pending and queue an ingest job for each. Returns the count."""
    from zk2.sources.service import enqueue_ingest  # noqa: PLC0415  (cycle: service -> orgs)

    settings = await get_org_settings(db, org_id=org_id)
    query = """
        SELECT DISTINCT s.id
        FROM sources s
        WHERE s.org_id = :org AND s.type <> 'directory'
    """
    params: dict[str, object] = {"org": org_id}
    if only_stale:
        query += """
          AND EXISTS (
              SELECT 1 FROM source_chunks sc
              JOIN source_embeddings se ON se.chunk_id = sc.id
              WHERE sc.source_id = s.id AND se.model <> :model
          )
        """
        params["model"] = settings.embedding_model

    source_ids = [row.id for row in (await db.execute(text(query), params)).all()]
    if not source_ids:
        return 0

    await db.execute(
        text("UPDATE sources SET status = 'pending', error = NULL WHERE id = ANY(:ids)"),
        {"ids": source_ids},
    )
    for source_id in source_ids:
        await enqueue_ingest(arq, db, source_id)
    logger.info("org.reindex_queued", org_id=org_id, count=len(source_ids))
    return len(source_ids)
