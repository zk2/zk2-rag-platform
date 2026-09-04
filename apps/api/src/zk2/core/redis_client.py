"""Redis client (single instance, async)."""

from __future__ import annotations

from redis.asyncio import Redis, from_url

from zk2.config import get_settings

_client: Redis | None = None


def get_redis() -> Redis:
    global _client
    if _client is None:
        settings = get_settings()
        # redis-py ships no annotations for from_url
        _client = from_url(  # type: ignore[no-untyped-call]
            str(settings.redis.url),
            decode_responses=True,
            health_check_interval=30,
        )
    return _client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
