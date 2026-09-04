"""Dense retrieval over pgvector.

Subtree-aware: when given source IDs that include directories, fans out to
descendants via ltree (sources.path).
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from zk2.retrieval.base import RetrievedChunk

SQL = """
    WITH allowed AS (
        SELECT id FROM sources
        WHERE org_id = :org
          AND path <@ ANY (
              SELECT path FROM sources
              WHERE id = ANY(:source_ids) AND org_id = :org
          )
          AND type != 'directory'
    )
    SELECT sc.id          AS chunk_id,
           sc.source_id   AS source_id,
           s.name         AS source_name,
           sc.ordinal     AS ordinal,
           sc.text        AS text,
           (se.embedding <=> :query_vec) AS distance
    FROM source_embeddings se
    JOIN source_chunks sc ON sc.id = se.chunk_id
    JOIN sources s ON s.id = sc.source_id
    WHERE sc.source_id IN (SELECT id FROM allowed)
      AND se.model = :model
    ORDER BY se.embedding <=> :query_vec
    LIMIT :k
"""


async def dense_search(
    db: AsyncSession,
    *,
    org_id: int,
    source_ids: list[int],
    query_embedding: list[float],
    embedding_model: str,
    k: int = 5,
) -> list[RetrievedChunk]:
    if not source_ids:
        return []
    rows = (
        await db.execute(
            text(SQL),
            {
                "org": org_id,
                "source_ids": source_ids,
                "query_vec": str(query_embedding),
                "model": embedding_model,
                "k": k,
            },
        )
    ).all()
    return [
        RetrievedChunk(
            chunk_id=r.chunk_id,
            source_id=r.source_id,
            source_name=r.source_name,
            ordinal=r.ordinal,
            text=r.text,
            # Cosine distance in [0, 2]; similarity is the useful direction
            score=1.0 - float(r.distance),
            retriever="dense",
        )
        for r in rows
    ]
