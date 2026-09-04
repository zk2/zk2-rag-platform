"""Prometheus metrics.

RED for HTTP (rate, errors, duration) plus the numbers that actually cost money
or degrade quality here: LLM latency, tokens, spend, retrieval stage timings,
ingest throughput and queue depth.

Cardinality is deliberate. HTTP metrics are labelled with the route template,
never the resolved path, and LLM spend is labelled by provider and model but
*not* by organization: per-org accounting lives in usage_events, where a row
per call is cheap and a query can group by anything. A Prometheus label with
one value per tenant is a time series per tenant, forever - see ADR-0010.
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

# Own registry rather than the global default: nothing else can accidentally
# register into it, and tests can read values without cross-test bleed.
REGISTRY = CollectorRegistry()

http_requests_total = Counter(
    "zk2_http_requests_total",
    "HTTP requests by route template and status",
    ["method", "route", "status"],
    registry=REGISTRY,
)

http_request_duration_seconds = Histogram(
    "zk2_http_request_duration_seconds",
    "HTTP request duration",
    ["method", "route"],
    registry=REGISTRY,
)

llm_request_duration_seconds = Histogram(
    "zk2_llm_request_duration_seconds",
    "Time from first byte sent to the provider until the stream ends",
    ["provider", "model"],
    buckets=(0.25, 0.5, 1, 2, 5, 10, 20, 40, 80),
    registry=REGISTRY,
)

llm_tokens_total = Counter(
    "zk2_llm_tokens_total",
    "Tokens exchanged with providers",
    ["provider", "model", "direction"],
    registry=REGISTRY,
)

llm_cost_usd_total = Counter(
    "zk2_llm_cost_usd_total",
    "Estimated spend in USD (models missing from the catalog are not counted)",
    ["provider", "model"],
    registry=REGISTRY,
)

llm_errors_total = Counter(
    "zk2_llm_errors_total",
    "Provider calls that failed",
    ["provider", "model"],
    registry=REGISTRY,
)

retrieval_duration_seconds = Histogram(
    "zk2_retrieval_duration_seconds",
    "Time spent per retrieval stage",
    ["stage"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5),
    registry=REGISTRY,
)

retrieval_results = Histogram(
    "zk2_retrieval_results",
    "Chunks returned per retrieval stage",
    ["stage"],
    buckets=(0, 1, 3, 5, 10, 20, 50, 100),
    registry=REGISTRY,
)

ingest_documents_total = Counter(
    "zk2_ingest_documents_total",
    "Documents processed by the ingest pipeline",
    ["status"],
    registry=REGISTRY,
)

ingest_duration_seconds = Histogram(
    "zk2_ingest_duration_seconds",
    "End-to-end ingest duration per document",
    buckets=(0.5, 1, 2, 5, 10, 30, 60, 120, 300),
    registry=REGISTRY,
)

ingest_chunks_total = Counter(
    "zk2_ingest_chunks_total",
    "Chunks written by the ingest pipeline",
    registry=REGISTRY,
)

active_websockets = Gauge(
    "zk2_active_websockets",
    "Chat WebSocket connections currently open",
    registry=REGISTRY,
)

ingest_queue_depth = Gauge(
    "zk2_ingest_queue_depth",
    "Jobs waiting in the Arq queue",
    registry=REGISTRY,
)


def record_llm_call(
    *,
    provider: str,
    model: str,
    duration_seconds: float,
    tokens_in: int,
    tokens_out: int,
    cost_usd: float | None,
) -> None:
    """One completed provider call."""
    llm_request_duration_seconds.labels(provider=provider, model=model).observe(duration_seconds)
    llm_tokens_total.labels(provider=provider, model=model, direction="in").inc(tokens_in)
    llm_tokens_total.labels(provider=provider, model=model, direction="out").inc(tokens_out)
    if cost_usd is not None:
        llm_cost_usd_total.labels(provider=provider, model=model).inc(cost_usd)


def record_retrieval(stage: str, *, duration_seconds: float, results: int) -> None:
    retrieval_duration_seconds.labels(stage=stage).observe(duration_seconds)
    retrieval_results.labels(stage=stage).observe(results)
