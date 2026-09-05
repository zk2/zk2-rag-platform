"""A per-address ceiling on the whole API.

Endpoint-specific limits protect the endpoints that matter; this one protects
the deployment from a client that has found any endpoint at all. Health checks
and the metrics scrape are exempt - they are called by machines on a schedule
and blocking them turns a traffic spike into a false outage.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import structlog
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from zk2.config import get_settings
from zk2.core.errors import RateLimitedError
from zk2.core.rate_limit import hit
from zk2.core.redis_client import get_redis

logger = structlog.get_logger()

Handler = Callable[[Request], Awaitable[Response]]
EXEMPT_PREFIXES = ("/health", "/metrics")


def client_ip(request: Request) -> str:
    """The caller's address, trusting X-Forwarded-For only behind a proxy."""
    settings = get_settings().app
    if settings.trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def global_rate_limit_middleware(request: Request, call_next: Handler) -> Response:
    settings = get_settings().auth
    if settings.rate_limit_global_per_min <= 0 or request.url.path.startswith(EXEMPT_PREFIXES):
        return await call_next(request)

    try:
        await hit(
            get_redis(),
            f"rl:global:{client_ip(request)}",
            limit=settings.rate_limit_global_per_min,
            window_seconds=60,
            error_message="Too many requests",
        )
    except RateLimitedError as exc:
        logger.warning("http.rate_limited", path=request.url.path)
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
            headers={"Retry-After": str(exc.retry_after)},
        )
    return await call_next(request)
