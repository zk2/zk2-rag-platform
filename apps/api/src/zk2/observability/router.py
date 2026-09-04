"""Metrics endpoint and the observability summary the UI shows.

/metrics is meant for a Prometheus scrape, not for the public internet. It is
unauthenticated by default because in a normal deployment it is only reachable
inside the cluster; setting METRICS_TOKEN turns on bearer-token checking for
deployments where that is not true.
"""

from __future__ import annotations

import secrets
from collections.abc import Awaitable
from typing import Annotated, cast

import structlog
from fastapi import APIRouter, Depends, Header, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from zk2.auth.rbac import OrgContext, require_org
from zk2.config import get_settings
from zk2.core.deps import get_db_dep
from zk2.core.errors import ForbiddenError, NotFoundError
from zk2.core.metrics import REGISTRY, ingest_queue_depth
from zk2.core.redis_client import get_redis
from zk2.observability.schemas import ModelUsageDto, ObservabilityLinks, UsageSummaryDto

logger = structlog.get_logger()

router = APIRouter(tags=["observability"])

_USAGE_SQL = """
    SELECT
        coalesce(sum(tokens_in), 0)              AS tokens_in,
        coalesce(sum(tokens_out), 0)             AS tokens_out,
        coalesce(sum(cost_usd), 0)               AS cost_usd,
        count(*)                                 AS calls,
        count(*) FILTER (WHERE cost_usd IS NULL) AS calls_without_price
    FROM usage_events
    WHERE org_id = :org AND created_at >= now() - make_interval(days => :days)
"""

_BY_MODEL_SQL = """
    SELECT provider, model,
           coalesce(sum(tokens_in + tokens_out), 0) AS tokens,
           coalesce(sum(cost_usd), 0)               AS cost_usd,
           count(*)                                 AS calls
    FROM usage_events
    WHERE org_id = :org AND created_at >= now() - make_interval(days => :days)
    GROUP BY provider, model
    ORDER BY sum(cost_usd) DESC NULLS LAST
    LIMIT 10
"""


async def _refresh_queue_depth() -> None:
    """Read the Arq queue length at scrape time instead of polling it."""
    try:
        # redis-py types llen as sync-or-awaitable; the async client returns a coroutine
        depth = cast("Awaitable[int]", get_redis().llen("arq:queue"))
        ingest_queue_depth.set(float(await depth))
    except Exception:
        logger.warning("metrics.queue_depth_unavailable")


@router.get("/metrics", include_in_schema=False)
async def metrics(authorization: Annotated[str | None, Header()] = None) -> Response:
    settings = get_settings().observability
    if not settings.metrics_enabled:
        raise NotFoundError("Metrics are disabled")
    if settings.metrics_token:
        expected = f"Bearer {settings.metrics_token.get_secret_value()}"
        if not authorization or not secrets.compare_digest(authorization, expected):
            raise ForbiddenError("Invalid metrics token")

    await _refresh_queue_depth()
    return Response(content=generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)


@router.get("/observability/usage", response_model=UsageSummaryDto)
async def usage_summary(
    ctx: Annotated[OrgContext, Depends(require_org("viewer"))],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    days: int = 7,
) -> UsageSummaryDto:
    """Tokens and spend for this organization over the last N days."""
    window = max(1, min(days, 90))
    totals = (await db.execute(text(_USAGE_SQL), {"org": ctx.org_id, "days": window})).one()
    by_model = (await db.execute(text(_BY_MODEL_SQL), {"org": ctx.org_id, "days": window})).all()
    return UsageSummaryDto(
        days=window,
        calls=totals.calls,
        tokens_in=totals.tokens_in,
        tokens_out=totals.tokens_out,
        cost_usd=str(totals.cost_usd),
        calls_without_price=totals.calls_without_price,
        by_model=[
            ModelUsageDto(
                provider=row.provider or "unknown",
                model=row.model or "unknown",
                calls=row.calls,
                tokens=row.tokens,
                cost_usd=str(row.cost_usd),
            )
            for row in by_model
        ],
    )


@router.get("/observability/links", response_model=ObservabilityLinks)
async def observability_links(
    _ctx: Annotated[OrgContext, Depends(require_org("viewer"))],
) -> ObservabilityLinks:
    """Where traces, dashboards and errors live for this deployment."""
    settings = get_settings().observability
    return ObservabilityLinks(
        grafana_url=settings.grafana_url,
        jaeger_url=settings.jaeger_url,
        langfuse_url=settings.langfuse_host if settings.langfuse_public_key else None,
        prometheus_url=settings.prometheus_url,
        sentry_enabled=bool(settings.sentry_dsn),
        tracing_enabled=bool(settings.otel_endpoint),
    )
