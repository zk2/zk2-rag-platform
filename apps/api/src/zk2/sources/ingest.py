"""Ingest pipeline: text → chunks → embeddings → tsvector → DB."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from zk2.core.storage import get_storage
from zk2.llm.registry import get_embedding_provider
from zk2.sources.chunking import chunk_text
from zk2.sources.loaders import load_bytes
from zk2.sources.models import (
    Source,
    SourceChunk,
    SourceEmbedding,
    SourceStatus,
    SourceType,
)
from zk2.sources.web import fetch_url

logger = structlog.get_logger()


async def ingest_source(db: AsyncSession, *, source_id: int) -> None:
    """Process a single source end-to-end. Idempotent: re-running re-indexes."""
    source = await db.scalar(select(Source).where(Source.id == source_id))
    if source is None:
        logger.warning("ingest.source_not_found", source_id=source_id)
        return
    if source.type == SourceType.DIRECTORY:
        return  # nothing to index

    source.status = SourceStatus.INDEXING
    source.error = None
    source.updated_at = datetime.now(UTC)
    await db.flush()

    try:
        text_content = await _extract(source)
        if not text_content.strip():
            raise ValueError("Extracted content is empty")

        await _clear_existing_chunks(db, source_id=source_id)

        chunks = chunk_text(text_content)
        if not chunks:
            raise ValueError("Chunking produced no chunks")

        chunk_rows = [
            SourceChunk(
                source_id=source_id,
                ordinal=c.ordinal,
                text=c.text,
                tokens=c.tokens,
                meta={"original_name": source.name},
            )
            for c in chunks
        ]
        db.add_all(chunk_rows)
        await db.flush()  # populate chunk_rows[*].id

        emb_provider = await get_embedding_provider(db, org_id=source.org_id)
        vectors = await emb_provider.embed_documents([c.text for c in chunks])
        if len(vectors) != len(chunk_rows):
            raise ValueError(f"Embedding count mismatch: {len(vectors)} vs {len(chunk_rows)}")

        db.add_all(
            SourceEmbedding(
                chunk_id=chunk.id,
                provider=emb_provider.name,
                model=emb_provider.model,
                embedding=vec,
            )
            for chunk, vec in zip(chunk_rows, vectors, strict=True)
        )

        # Populate BM25 via SQL (uses Postgres' to_tsvector)
        await db.execute(
            text(
                """
                INSERT INTO source_bm25 (chunk_id, tsv)
                SELECT id, to_tsvector('english', text)
                FROM source_chunks
                WHERE source_id = :sid
                ON CONFLICT (chunk_id) DO UPDATE SET tsv = EXCLUDED.tsv
                """
            ),
            {"sid": source_id},
        )

        source.status = SourceStatus.READY
        source.updated_at = datetime.now(UTC)
        meta: dict[str, Any] = dict(source.meta or {})
        meta["chunks"] = len(chunk_rows)
        meta["tokens"] = sum(c.tokens for c in chunks)
        source.meta = meta
        logger.info(
            "ingest.ok",
            source_id=source_id,
            name=source.name,
            chunks=len(chunk_rows),
            tokens=meta["tokens"],
        )
    except Exception as exc:
        source.status = SourceStatus.FAILED
        source.error = str(exc)[:1900]
        source.updated_at = datetime.now(UTC)
        logger.exception("ingest.failed", source_id=source_id)


async def _extract(source: Source) -> str:
    if source.type == SourceType.FILE:
        key = (source.meta or {}).get("storage_key")
        if not key:
            raise ValueError("File source missing storage_key in metadata")
        raw = await get_storage().read(key)
        return load_bytes(raw, source.name)

    if source.type == SourceType.WEB:
        url = (source.meta or {}).get("url") or source.name
        page = await fetch_url(url)
        return page.text

    raise ValueError(f"Unsupported source type for ingest: {source.type}")


async def _clear_existing_chunks(db: AsyncSession, *, source_id: int) -> None:
    """When re-indexing, drop previous chunks (cascade clears embeddings + bm25)."""
    await db.execute(delete(SourceChunk).where(SourceChunk.source_id == source_id))
    await db.flush()
