"""Source CRUD + tree building."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

import structlog
from arq import ArqRedis
from sqlalchemy import CursorResult, Row, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from zk2.core.errors import NotFoundError, ValidationError
from zk2.core.net import assert_url_allowed
from zk2.core.storage import get_storage
from zk2.sources.ingest import ingest_source
from zk2.sources.models import Source, SourceStatus, SourceType
from zk2.sources.web import discover_sitemap

logger = structlog.get_logger()

# One node of the sources tree as returned to the API layer.
TreeNode = dict[str, Any]


async def _resolve_path(
    db: AsyncSession, *, org_id: int, parent_id: int | None, child_id: int
) -> str:
    """Compute the ltree path for a newly inserted node given its parent."""
    if parent_id is None:
        return str(child_id)
    parent = await db.scalar(select(Source).where(Source.id == parent_id, Source.org_id == org_id))
    if parent is None:
        raise NotFoundError("Parent directory not found")
    if parent.type != SourceType.DIRECTORY:
        raise ValidationError("Parent must be a directory")
    return f"{parent.path}.{child_id}"


async def create_directory(
    db: AsyncSession, *, org_id: int, name: str, parent_id: int | None
) -> Source:
    row = Source(
        org_id=org_id,
        type=SourceType.DIRECTORY.value,
        name=name.strip(),
        path="0",  # placeholder, replaced below
        status=SourceStatus.READY.value,
    )
    db.add(row)
    await db.flush()
    row.path = await _resolve_path(db, org_id=org_id, parent_id=parent_id, child_id=row.id)
    await db.flush()
    return row


async def create_file_source(
    db: AsyncSession,
    arq: ArqRedis | None,
    *,
    org_id: int,
    parent_id: int | None,
    filename: str,
    content: bytes,
) -> Source:
    storage_key = await get_storage().save(
        org_id, content, suffix=f".{filename.rsplit('.', 1)[-1]}" if "." in filename else ""
    )
    row = Source(
        org_id=org_id,
        type=SourceType.FILE.value,
        name=filename,
        path="0",
        status=SourceStatus.PENDING.value,
        meta={"storage_key": storage_key, "size": len(content)},
    )
    db.add(row)
    await db.flush()
    row.path = await _resolve_path(db, org_id=org_id, parent_id=parent_id, child_id=row.id)
    await db.flush()
    await enqueue_ingest(arq, db, row.id)
    return row


async def create_url_source(
    db: AsyncSession,
    arq: ArqRedis | None,
    *,
    org_id: int,
    parent_id: int | None,
    url: str,
) -> Source:
    # Validate before the row exists: the user gets the error now, not as a
    # failed ingest job minutes later.
    await assert_url_allowed(url)
    row = Source(
        org_id=org_id,
        type=SourceType.WEB.value,
        name=url,
        path="0",
        status=SourceStatus.PENDING.value,
        meta={"url": url},
    )
    db.add(row)
    await db.flush()
    row.path = await _resolve_path(db, org_id=org_id, parent_id=parent_id, child_id=row.id)
    await db.flush()
    await enqueue_ingest(arq, db, row.id)
    return row


async def import_sitemap(
    db: AsyncSession,
    arq: ArqRedis | None,
    *,
    org_id: int,
    parent_id: int | None,
    base_url: str,
    limit: int,
) -> list[Source]:
    urls = await discover_sitemap(base_url, limit=limit)
    if not urls:
        raise ValidationError("Sitemap discovery returned no URLs")
    created: list[Source] = []
    for url in urls:
        created.append(
            await create_url_source(db, arq, org_id=org_id, parent_id=parent_id, url=url)
        )
    return created


async def reindex_source(
    db: AsyncSession, arq: ArqRedis | None, *, org_id: int, source_id: int
) -> Source:
    """Queue a single source for re-ingestion."""
    source = await db.scalar(select(Source).where(Source.id == source_id, Source.org_id == org_id))
    if source is None:
        raise NotFoundError("Source not found")
    if source.type == SourceType.DIRECTORY.value:
        raise ValidationError("A directory has nothing to index; reindex its documents")
    source.status = SourceStatus.PENDING.value
    source.error = None
    await db.flush()
    await enqueue_ingest(arq, db, source.id)
    return source


async def delete_source(db: AsyncSession, *, org_id: int, source_id: int) -> int:
    source = await db.scalar(select(Source).where(Source.id == source_id, Source.org_id == org_id))
    if source is None:
        raise NotFoundError("Source not found")
    # Subtree: same path or descendant
    rows = (
        await db.execute(
            text(
                "SELECT id, type, (metadata->>'storage_key') AS storage_key "
                "FROM sources WHERE org_id = :org AND path <@ :p"
            ),
            {"org": org_id, "p": source.path},
        )
    ).all()
    storage = get_storage()
    for r in rows:
        if r.storage_key:
            try:
                await storage.delete(r.storage_key)
            except Exception:
                logger.warning("storage.delete_failed", key=r.storage_key)
    result = cast(
        "CursorResult[Any]",
        await db.execute(
            text("DELETE FROM sources WHERE org_id = :org AND path <@ :p"),
            {"org": org_id, "p": source.path},
        ),
    )
    return result.rowcount or 0


async def get_tree(
    db: AsyncSession, *, org_id: int, parent_id: int | None, directories_only: bool
) -> list[TreeNode]:
    base_path = "*"
    if parent_id is not None:
        parent = await db.scalar(
            select(Source).where(Source.id == parent_id, Source.org_id == org_id)
        )
        if parent is None:
            raise NotFoundError("Parent not found")
        # `12.*` matches the parent itself too; `12.*{1,}` is strict descendants
        base_path = f"{parent.path}.*{{1,}}"

    # asyncpg parses `:` as parameter prefix, so use CAST(...) instead of `::lquery`.
    # Also use CAST(path AS text) to render the ltree value as a string.
    # Every fragment of `where_parts` is a literal defined right here, and every
    # value travels as a bound parameter - nothing user-supplied is formatted in.
    where_parts = ["org_id = :org", "path ~ CAST(:p AS lquery)"]
    params: dict[str, object] = {"org": org_id, "p": base_path}
    if directories_only:
        where_parts.append("type = 'directory'")
    where_sql = " AND ".join(where_parts)
    sql = (
        "SELECT id, type, name, status, CAST(path AS text) AS path "  # noqa: S608
        f"FROM sources WHERE {where_sql} ORDER BY nlevel(path), name"
    )
    rows = (await db.execute(text(sql), params)).all()
    return _build_tree(rows)


def _build_tree(rows: Sequence[Row[Any]]) -> list[TreeNode]:
    by_path: dict[str, TreeNode] = {}
    roots: list[TreeNode] = []
    # Sorted by nlevel(path) so parents are seen before children.
    for r in rows:
        node = {
            "id": r.id,
            "type": r.type,
            "name": r.name,
            "status": r.status,
            "children": [],
        }
        by_path[r.path] = node
        parent_path = r.path.rsplit(".", 1)[0] if "." in r.path else None
        if parent_path and parent_path in by_path:
            by_path[parent_path]["children"].append(node)
        else:
            roots.append(node)
    return roots


async def enqueue_ingest(arq: ArqRedis | None, db: AsyncSession, source_id: int) -> None:
    """Enqueue Arq job; if arq is unavailable, run inline (useful for tests)."""
    if arq is not None:
        await arq.enqueue_job("ingest_source", source_id)
        return
    # Inline fallback
    await ingest_source(db, source_id=source_id)
