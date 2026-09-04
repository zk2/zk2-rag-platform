"""DTOs for the observability endpoints."""

from __future__ import annotations

from pydantic import BaseModel


class ModelUsageDto(BaseModel):
    provider: str
    model: str
    calls: int
    tokens: int
    cost_usd: str


class UsageSummaryDto(BaseModel):
    days: int
    calls: int
    tokens_in: int
    tokens_out: int
    cost_usd: str
    # Calls whose model is not in the catalog: their spend is unknown, not zero
    calls_without_price: int
    by_model: list[ModelUsageDto]


class ObservabilityLinks(BaseModel):
    grafana_url: str | None
    jaeger_url: str | None
    langfuse_url: str | None
    prometheus_url: str | None
    sentry_enabled: bool
    tracing_enabled: bool
