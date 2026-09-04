"""Arq worker entrypoints. Import WorkerSettings to start: `arq zk2.jobs.WorkerSettings`."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any, ClassVar

import structlog
from arq.connections import RedisSettings

from zk2.config import get_settings
from zk2.core.db import db_session, dispose_engine
from zk2.core.logging import configure_logging
from zk2.sources.ingest import ingest_source as _ingest_source

logger = structlog.get_logger()


async def ingest_source(_: dict[str, Any], source_id: int) -> None:
    async with db_session() as db:
        await _ingest_source(db, source_id=source_id)


async def on_startup(_: dict[str, Any]) -> None:
    configure_logging()
    logger.info("arq.worker.start")


async def on_shutdown(_: dict[str, Any]) -> None:
    await dispose_engine()
    logger.info("arq.worker.stop")


def _redis_settings() -> RedisSettings:
    url = str(get_settings().redis.url)
    return RedisSettings.from_dsn(url)


class WorkerSettings:
    functions: ClassVar[list[Callable[..., Coroutine[Any, Any, None]]]] = [ingest_source]
    on_startup = on_startup
    on_shutdown = on_shutdown
    redis_settings = _redis_settings()
    keep_result = 3600  # seconds
