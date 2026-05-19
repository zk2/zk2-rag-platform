"""Arq Redis pool — shared client for enqueuing jobs from the API."""

from __future__ import annotations

from arq import ArqRedis, create_pool
from arq.connections import RedisSettings

from zk2.config import get_settings

_pool: ArqRedis | None = None


async def get_arq() -> ArqRedis:
    global _pool
    if _pool is None:
        url = str(get_settings().redis.url)
        _pool = await create_pool(RedisSettings.from_dsn(url))
    return _pool


async def close_arq() -> None:
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None


async def get_arq_dep() -> ArqRedis:
    return await get_arq()
