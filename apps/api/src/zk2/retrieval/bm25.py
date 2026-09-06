"""Lexical retrieval over Postgres full-text search.

Complements dense retrieval: exact terms, names, error codes and numbers are
where embeddings are weakest. The text search configuration comes from the
source itself (detected at ingest), so the query is analysed the same way the
document was - an English query against Russian-stemmed documents matches
nothing, and vice versa.

Two things about turning a question into a `tsquery` decide whether this
retriever contributes anything at all.

`plainto_tsquery` joins every word with AND. "Чем Langfuse отличается от
LangSmith по возможностям?" becomes a demand for all seven words in one chunk,
including "чем", "от" and "по" - so it matched nothing, on every question
anyone would actually type. Measured on the corpus: zero hits for every
natural-language question, and hits only for bare keywords. Half of hybrid
retrieval was returning an empty list into the fusion.

OR alone is not enough either. A stopword is only stripped by its own
language's configuration, so under `english` the Russian "чем" survives as a
search term and matches nearly every chunk, burying the one that actually holds
the rare word. A word that any configured language calls a stopword is
therefore dropped before the query is built.

What remains is honest lexical matching, ranked by `ts_rank_cd`. Postgres has
no IDF, so a rare term does not outweigh a common one the way real BM25 would;
that is what fusion with dense retrieval is for.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from zk2.retrieval.base import RetrievedChunk
from zk2.retrieval.location import chunk_location as _location

SQL = """
    WITH allowed AS (
        SELECT id, lang_config FROM sources
        WHERE org_id = :org
          AND path <@ ANY (
              SELECT path FROM sources
              WHERE id = ANY(:source_ids) AND org_id = :org
          )
          AND type != 'directory'
    ),
    configs AS (
        SELECT DISTINCT lang_config FROM allowed
    ),
    -- Alphanumerics only, so nothing here can be read as a tsquery operator
    words AS (
        SELECT DISTINCT w
        FROM unnest(regexp_split_to_array(lower(:q), '[^[:alnum:]_]+')) AS w
        WHERE w <> ''
    ),
    useful AS (
        SELECT w FROM words
        WHERE NOT EXISTS (
            SELECT 1 FROM configs
            WHERE to_tsvector(CAST(configs.lang_config AS regconfig), words.w) = ''::tsvector
        )
    ),
    queries AS (
        SELECT c.lang_config,
               to_tsquery(
                   CAST(c.lang_config AS regconfig),
                   (SELECT string_agg(w, ' | ') FROM useful)
               ) AS tsq
        FROM configs c
        WHERE EXISTS (SELECT 1 FROM useful)
    )
    SELECT sc.id        AS chunk_id,
           sc.source_id AS source_id,
           s.name       AS source_name,
           sc.ordinal   AS ordinal,
           sc.text      AS text,
           sc.metadata  AS meta,
           ts_rank_cd(b.tsv, q.tsq) AS rank
    FROM source_bm25 b
    JOIN source_chunks sc ON sc.id = b.chunk_id
    JOIN allowed a ON a.id = sc.source_id
    JOIN sources s ON s.id = sc.source_id
    JOIN queries q ON q.lang_config = a.lang_config
    WHERE b.tsv @@ q.tsq
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
            **_location(r.meta),
            score=float(r.rank),
            retriever="bm25",
        )
        for r in rows
    ]
