"""Arq worker entrypoints. Import WorkerSettings to start: `arq zk2.jobs.WorkerSettings`."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any, ClassVar

import structlog
from arq import cron
from arq.connections import RedisSettings
from sqlalchemy import select

from zk2.agents.mcp_client import check_server
from zk2.agents.models import McpServer
from zk2.config import get_settings
from zk2.core.db import db_session, dispose_engine
from zk2.core.logging import configure_logging
from zk2.core.tracing import flush_traces
from zk2.evals.runner import RunSettings, execute_run
from zk2.sources.ingest import ingest_source as _ingest_source

logger = structlog.get_logger()


async def ingest_source(_: dict[str, Any], source_id: int) -> None:
    async with db_session() as db:
        await _ingest_source(db, source_id=source_id)


async def run_eval(_: dict[str, Any], run_id: int, settings: dict[str, Any]) -> None:
    """Execute an evaluation run in the worker: it is long and costs money."""
    async with db_session() as db:
        await execute_run(
            db,
            run_id=run_id,
            settings=RunSettings(
                metrics=settings.get("metrics", []),
                judge_provider=settings.get("judge_provider", "openai"),
                judge_model=settings.get("judge_model", "gpt-4.1-mini"),
            ),
        )


async def check_mcp_servers(_: dict[str, Any]) -> None:
    """Periodic MCP health check.

    Servers belong to other people and go down without telling us; the settings
    page should say so before an agent turn discovers it.
    """
    async with db_session() as db:
        servers = (
            (await db.execute(select(McpServer).where(McpServer.enabled.is_(True)))).scalars().all()
        )
        for server in servers:
            await check_server(db, server)
    logger.info("arq.mcp_checked", servers=len(servers))


async def on_startup(_: dict[str, Any]) -> None:
    configure_logging()
    logger.info("arq.worker.start")


async def on_shutdown(_: dict[str, Any]) -> None:
    await flush_traces()
    await dispose_engine()
    logger.info("arq.worker.stop")


def _redis_settings() -> RedisSettings:
    url = str(get_settings().redis.url)
    return RedisSettings.from_dsn(url)


class WorkerSettings:
    functions: ClassVar[list[Callable[..., Coroutine[Any, Any, None]]]] = [
        ingest_source,
        check_mcp_servers,
        run_eval,
    ]
    cron_jobs: ClassVar[list[Any]] = [
        cron(check_mcp_servers, minute=set(range(0, 60, 5)), run_at_startup=False)  # type: ignore[arg-type]
    ]
    on_startup = on_startup
    on_shutdown = on_shutdown
    redis_settings = _redis_settings()
    keep_result = 3600  # seconds
