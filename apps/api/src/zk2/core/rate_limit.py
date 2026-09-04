"""Token-bucket rate limiting via Redis.

Sliding window approximation using fixed window + atomic INCR with TTL.
Good enough for auth endpoints; for production-grade switch to a real sliding window.
"""

from __future__ import annotations

from redis.asyncio import Redis

from zk2.core.errors import RateLimitedError


async def hit(
    redis: Redis,
    key: str,
    *,
    limit: int,
    window_seconds: int,
    error_message: str = "Rate limit exceeded",
) -> None:
    """Raise RateLimitedError if `key` has been hit more than `limit` times within window."""
    current = await redis.incr(key)
    if current == 1:
        await redis.expire(key, window_seconds)
    if current > limit:
        ttl = await redis.ttl(key)
        raise RateLimitedError(error_message, retry_after=max(ttl, 1))
