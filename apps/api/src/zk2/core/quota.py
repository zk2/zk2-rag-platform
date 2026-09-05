"""The allowance for deployment-owned provider keys.

An organization can start without any keys of its own: the deployment's
fallback keys serve it up to a token allowance, and after that it has to bring
its own. This is not billing - nobody is charged anything - it is a cap on
somebody else's spending.

The count is authoritative in `usage_events` (every call writes a row saying
which key paid) and cached in Redis for the hot path, where a turn needs the
answer before it starts rather than a table scan.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from zk2.config import get_settings
from zk2.core.errors import AppError
from zk2.core.redis_client import get_redis

logger = structlog.get_logger()

COUNTER_TTL_SECONDS = 3600
_COUNTER_PREFIX = "quota:system-tokens"


class KeySource(StrEnum):
    ORG = "org"
    SYSTEM = "system"


class SystemKeyExhaustedError(AppError):
    """The deployment's keys have served this organization all they will."""

    status_code = 403
    code = "system_key_limit_reached"


@dataclass(frozen=True, slots=True)
class Allowance:
    limit: int
    used: int
    enabled: bool

    @property
    def remaining(self) -> int:
        return max(self.limit - self.used, 0)

    @property
    def exhausted(self) -> bool:
        return self.enabled and self.used >= self.limit


_USED_SQL = """
    SELECT coalesce(sum(tokens_in + tokens_out), 0) AS used
    FROM usage_events
    WHERE org_id = :org
      AND key_source = 'system'
      AND (:days = 0 OR created_at >= now() - make_interval(days => :days))
"""


def _counter_key(org_id: int) -> str:
    return f"{_COUNTER_PREFIX}:{org_id}"


async def tokens_used(db: AsyncSession, *, org_id: int) -> int:
    """Tokens this organization has taken from the deployment's keys."""
    settings = get_settings().quota
    redis = get_redis()
    key = _counter_key(org_id)

    cached = await redis.get(key)
    if cached is not None:
        return int(cached)

    used = (
        await db.execute(text(_USED_SQL), {"org": org_id, "days": settings.system_key_window_days})
    ).scalar_one()
    await redis.set(key, int(used), ex=COUNTER_TTL_SECONDS)
    return int(used)


async def record_system_tokens(org_id: int, tokens: int) -> None:
    """Keep the cached counter in step with the row the caller just wrote."""
    if tokens <= 0:
        return
    redis = get_redis()
    key = _counter_key(org_id)
    if await redis.exists(key):
        await redis.incrby(key, tokens)


async def allowance(db: AsyncSession, *, org_id: int) -> Allowance:
    settings = get_settings().quota
    return Allowance(
        limit=settings.system_key_token_limit,
        used=await tokens_used(db, org_id=org_id),
        enabled=settings.system_keys_enabled,
    )


async def ensure_system_key_allowed(db: AsyncSession, *, org_id: int, provider: str) -> None:
    """Raise before a call that would be paid for by an exhausted allowance."""
    settings = get_settings().quota
    if not settings.system_keys_enabled:
        raise SystemKeyExhaustedError(
            f"This deployment does not lend its {provider} key. "
            f"Add your own key in Settings -> Providers."
        )
    current = await allowance(db, org_id=org_id)
    if current.exhausted:
        logger.info("quota.system_keys_exhausted", org_id=org_id, used=current.used)
        raise SystemKeyExhaustedError(
            f"The shared {provider} key has served this organization its "
            f"{current.limit:,} token allowance. Add your own key in "
            f"Settings -> Providers to continue."
        )
