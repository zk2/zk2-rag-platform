"""Lexical retrieval over Postgres full-text search.

Complements dense retrieval: exact terms, names, error codes and numbers are
where embeddings are weakest. The text search configuration comes from the
source itself (detected at ingest), so the query is analysed the same way the
document was - an English query against Russian-stemmed documents matches
nothing, and vice versa.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from zk2.retrieval.base import RetrievedChunk

SQL = """
    WITH allowed AS (
        SELECT id, lang_config FROM sources
        WHERE org_id = :org
          AND path <@ ANY (
              SELECT path FROM sources
              WHERE id = ANY(:source_ids) AND org_id = :org
          )
          AND type != 'directory'
    )
    SELECT sc.id        AS chunk_id,
           sc.source_id AS source_id,
           s.name       AS source_name,
           sc.ordinal   AS ordinal,
           sc.text      AS text,
           ts_rank_cd(b.tsv, plainto_tsquery(CAST(a.lang_config AS regconfig), :q)) AS rank
    FROM source_bm25 b
    JOIN source_chunks sc ON sc.id = b.chunk_id
    JOIN allowed a ON a.id = sc.source_id
    JOIN sources s ON s.id = sc.source_id
    WHERE b.tsv @@ plainto_tsquery(CAST(a.lang_config AS regconfig), :q)
    ORDER BY rank DESC
    LIMIT :k
"""


async def bm25_search(
    db: AsyncSession,
    *,
    org_id: int,
    source_ids: list[int],
    query: str,
    k: int = 5,
) -> list[RetrievedChunk]:
    if not source_ids or not query.strip():
        return []
    rows = (
        await db.execute(
            text(SQL),
            {"org": org_id, "source_ids": source_ids, "q": query, "k": k},
        )
    ).all()
    return [
        RetrievedChunk(
            chunk_id=r.chunk_id,
            source_id=r.source_id,
            source_name=r.source_name,
            ordinal=r.ordinal,
            text=r.text,
            score=float(r.rank),
            retriever="bm25",
        )
        for r in rows
    ]
