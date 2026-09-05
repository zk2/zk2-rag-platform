"""The read-only connection the sql_query tool uses.

Separate engine, separate database user, its own transaction settings. The
application's own credentials are never used here: a tool whose input is
written by a language model does not get a connection that can write.
"""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from zk2.config import get_settings

logger = structlog.get_logger()

MAX_ROWS = 50

_engine: AsyncEngine | None = None


def get_readonly_engine() -> AsyncEngine | None:
    global _engine
    settings = get_settings().tools
    if settings.readonly_database_url is None:
        return None
    if _engine is None:
        _engine = create_async_engine(
            settings.readonly_database_url.get_secret_value(),
            pool_size=2,
            max_overflow=0,
            pool_pre_ping=True,
        )
    return _engine


async def dispose_readonly_engine() -> None:
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None


def _format(rows: list[Any], columns: list[str]) -> str:
    if not rows:
        return "0 rows."
    header = " | ".join(columns)
    body = "\n".join(" | ".join("" if v is None else str(v) for v in row) for row in rows)
    suffix = f"\n... more rows omitted (limit {MAX_ROWS})" if len(rows) == MAX_ROWS else ""
    return f"{header}\n{body}{suffix}"


async def run_readonly_query(statement: str) -> str:
    engine = get_readonly_engine()
    if engine is None:
        return "The SQL tool is not configured for this deployment."

    timeout_ms = get_settings().tools.sql_timeout_ms
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SET TRANSACTION READ ONLY"))
            await conn.execute(text(f"SET LOCAL statement_timeout = {int(timeout_ms)}"))
            result = await conn.execute(text(statement))
            rows = result.fetchmany(MAX_ROWS)
            columns = list(result.keys())
    except Exception as exc:
        logger.warning("tools.sql_failed", error=str(exc)[:200])
        return f"Query failed: {str(exc)[:400]}"
    return _format([tuple(row) for row in rows], columns)
