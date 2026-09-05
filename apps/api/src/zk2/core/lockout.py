"""Brute-force lockout for password login.

A per-minute rate limit slows an attacker down; it does not stop a patient one.
After a number of failed attempts an account is locked for a while, counted per
email and per source address so that neither one alone can be used to lock
somebody else out cheaply:

* the email counter stops credential stuffing against one account
* the IP counter stops a spray across many accounts from one place

Counters are cleared on a successful login, so a user who mistypes twice and
then gets it right is never affected.
"""

from __future__ import annotations

import structlog
from redis.asyncio import Redis

from zk2.config import get_settings
from zk2.core.errors import RateLimitedError

logger = structlog.get_logger()

_EMAIL_PREFIX = "lockout:email"
_IP_PREFIX = "lockout:ip"


def _email_key(email: str) -> str:
    return f"{_EMAIL_PREFIX}:{email.lower()}"


def _ip_key(ip: str) -> str:
    return f"{_IP_PREFIX}:{ip}"


async def assert_not_locked(redis: Redis, *, email: str, ip: str | None) -> None:
    """Refuse before checking the password, so a locked account leaks nothing."""
    settings = get_settings().auth
    for key, subject in ((_email_key(email), "account"), (_ip_key(ip or "unknown"), "address")):
        raw = await redis.get(key)
        if raw is not None and int(raw) >= settings.lockout_threshold:
            ttl = await redis.ttl(key)
            logger.warning("auth.locked_out", subject=subject, ip=ip)
            raise RateLimitedError(
                f"Too many failed attempts for this {subject}. Try again later.",
                retry_after=max(ttl, 1),
            )


async def record_failure(redis: Redis, *, email: str, ip: str | None) -> None:
    settings = get_settings().auth
    window = settings.lockout_minutes * 60
    for key in (_email_key(email), _ip_key(ip or "unknown")):
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, window)
        elif count == settings.lockout_threshold:
            # Restart the clock when the threshold is reached: the lock lasts
            # from the last failure, not from the first
            await redis.expire(key, window)


async def clear(redis: Redis, *, email: str, ip: str | None) -> None:
    await redis.delete(_email_key(email), _ip_key(ip or "unknown"))
