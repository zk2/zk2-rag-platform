"""Organization settings, and the reindex they can require.

Dense retrieval filters on the embedding model that produced a vector. Changing
the model without rebuilding the index does not raise anything - it just
silently returns nothing, which is the worst way for a search feature to fail.
So the settings API reports how much of the corpus is stale, and reindexing is
an explicit, visible operation.

Chunking is the same kind of setting: cutting documents differently changes
every stored chunk, and so does a shipped change to the chunker itself. All
three - model, chunking settings, chunker version - feed one definition of
stale, because a corpus indexed under any older answer needs the same redoing.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog
from arq import ArqRedis
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from zk2.config import get_settings
from zk2.core.errors import ValidationError
from zk2.llm.catalog import get_catalog
from zk2.llm.registry import embedding_model_support
from zk2.orgs.models import OrgSettings
from zk2.orgs.schemas import EmbeddingSettingsDto
from zk2.sources.chunking import CHUNKER_VERSION, ChunkStrategy

logger = structlog.get_logger()

# A source is stale when anything about how it was indexed differs from what
# the organization is configured for now. `sources.metadata` records the chunking
# it was built with; a row that predates that stamp has no key at all, which
# compares unequal and is exactly right - it was built by an older chunker.
#
# One query answers it for every source, and the callers count or filter the
# rows themselves: composing the predicate into two larger statements would
# mean building SQL by interpolation, which this codebase does not do.
_SOURCE_INDEX_STATE_SQL = """
    SELECT
        s.id,
        coalesce(bool_or(se.id IS NOT NULL), false)      AS indexed,
        coalesce(bool_or(se.model <> :model), false)     AS model_stale,
        (
            coalesce((s.metadata ->> 'chunker_version')::int, 0) <> :chunker_version
            OR coalesce((s.metadata ->> 'chunk_size')::int, 0) <> :chunk_size
            OR coalesce((s.metadata ->> 'chunk_overlap')::int, -1) <> :chunk_overlap
            OR coalesce(s.metadata ->> 'chunk_strategy', '') <> :chunk_strategy
        )                                                AS settings_stale
    FROM sources s
    JOIN source_chunks sc ON sc.source_id = s.id
    LEFT JOIN source_embeddings se ON se.chunk_id = sc.id
    WHERE s.org_id = :org
    GROUP BY s.id, s.metadata
"""


@dataclass(frozen=True, slots=True)
class _SourceState:
    id: int
    indexed: bool
    stale: bool


async def _index_state(
    db: AsyncSession, *, org_id: int, settings: OrgSettings
) -> list[_SourceState]:
    rows = await db.execute(
        text(_SOURCE_INDEX_STATE_SQL),
        {
            "org": org_id,
            "model": settings.embedding_model,
            "chunker_version": CHUNKER_VERSION,
            "chunk_size": settings.chunk_size,
            "chunk_overlap": settings.chunk_overlap,
            "chunk_strategy": settings.chunk_strategy,
        },
    )
    return [
        _SourceState(
            id=row.id,
            indexed=row.indexed,
            stale=row.indexed and (row.model_stale or row.settings_stale),
        )
        for row in rows.all()
    ]


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
    state = await _index_state(db, org_id=org_id, settings=settings)
    spec = get_catalog().embedding_model(settings.embedding_model)
    return EmbeddingSettingsDto(
        embedding_provider=settings.embedding_provider,
        embedding_model=settings.embedding_model,
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        chunk_strategy=ChunkStrategy(settings.chunk_strategy),
        # The width vectors are stored at, not the model's native size: a
        # 3072-wide model is asked to truncate, and reporting 3072 here
        # described an index that has never existed. See ADR-0004.
        dimensions=get_settings().ingest.embedding_dimensions if spec else 0,
        indexed_sources=sum(1 for row in state if row.indexed),
        stale_sources=sum(1 for row in state if row.stale),
    )


async def update_embedding_settings(
    db: AsyncSession, *, org_id: int, provider: str, model: str
) -> EmbeddingSettingsDto:
    spec = get_catalog().embedding_model(model)
    if spec is None:
        raise ValidationError(f"Unknown embedding model: {model}")
    # Saving a model the ingest path will refuse is a setting that breaks
    # every upload and every query afterwards, with no error until then.
    reason = embedding_model_support(spec)
    if reason is not None:
        raise ValidationError(f"{spec.display_name} cannot be used here: {reason}")

    settings = await get_org_settings(db, org_id=org_id)
    settings.embedding_provider = provider
    settings.embedding_model = model
    await db.flush()
    logger.info("org.embedding_model_changed", org_id=org_id, model=model)
    return await describe_embedding_settings(db, org_id=org_id)


async def update_chunking_settings(
    db: AsyncSession,
    *,
    org_id: int,
    chunk_size: int,
    chunk_overlap: int,
    chunk_strategy: ChunkStrategy,
) -> EmbeddingSettingsDto:
    """Change how documents are cut. Every existing chunk becomes stale."""
    if chunk_overlap >= chunk_size:
        raise ValidationError("chunk_overlap must be smaller than chunk_size")

    settings = await get_org_settings(db, org_id=org_id)
    settings.chunk_size = chunk_size
    settings.chunk_overlap = chunk_overlap
    settings.chunk_strategy = chunk_strategy.value
    await db.flush()
    logger.info(
        "org.chunking_changed",
        org_id=org_id,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        strategy=chunk_strategy.value,
    )
    return await describe_embedding_settings(db, org_id=org_id)


async def queue_reindex(
    db: AsyncSession, arq: ArqRedis | None, *, org_id: int, only_stale: bool = False
) -> int:
    """Mark sources pending and queue an ingest job for each. Returns the count."""
    from zk2.sources.service import enqueue_ingest  # noqa: PLC0415  (cycle: service -> orgs)

    settings = await get_org_settings(db, org_id=org_id)
    if only_stale:
        source_ids = [
            row.id for row in await _index_state(db, org_id=org_id, settings=settings) if row.stale
        ]
    else:
        rows = await db.execute(
            text(
                """
                SELECT id FROM sources
                WHERE org_id = :org AND type <> 'directory'
                """
            ),
            {"org": org_id},
        )
        source_ids = [row.id for row in rows.all()]

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
