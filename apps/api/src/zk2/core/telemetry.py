"""OpenTelemetry + Sentry setup."""

from __future__ import annotations

from typing import TYPE_CHECKING

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import (
    Decision,
    ParentBased,
    Sampler,
    SamplingResult,
)
from opentelemetry.trace import Link, SpanKind
from opentelemetry.util.types import Attributes

from zk2.config import get_settings

if TYPE_CHECKING:
    from collections.abc import Sequence

    from fastapi import FastAPI
    from opentelemetry.trace.span import TraceState

#: Endpoints polled on a timer by machines, not people.
_UNINTERESTING_PATHS = ("/health", "/metrics")


class _DropPlumbing(Sampler):
    """Keep traces that describe a request; drop the machinery around them.

    Two kinds of trace arrive with nothing above them and no story to tell.
    Prometheus scrapes /metrics every fifteen seconds and the load balancer
    polls /health, which between them produced two hundred identical
    two-millisecond traces an hour. And a client span with no parent - the
    queue poller's Redis LLEN, a connection check - is plumbing that belongs to
    no request at all; each one became a trace of its own.

    Only root spans reach this sampler: it is wrapped in ParentBased, so
    everything inside a kept request is kept, and everything inside a dropped
    one goes with it.
    """

    def should_sample(
        self,
        parent_context: Context | None,
        trace_id: int,
        name: str,
        kind: SpanKind | None = None,
        attributes: Attributes = None,
        links: Sequence[Link] | None = None,
        trace_state: TraceState | None = None,
    ) -> SamplingResult:
        # The signature is the SDK's; most of it is of no interest to this rule
        del parent_context, trace_id, name, links
        attrs = attributes or {}
        path = str(
            attrs.get("url.path") or attrs.get("http.route") or attrs.get("http.target") or ""
        )
        noisy = path.startswith(_UNINTERESTING_PATHS) or kind is SpanKind.CLIENT
        decision = Decision.DROP if noisy else Decision.RECORD_AND_SAMPLE
        return SamplingResult(decision, attributes, trace_state)

    def get_description(self) -> str:
        return "zk2:drop-plumbing"


def setup_telemetry(app: FastAPI) -> None:
    settings = get_settings()

    if settings.observability.otel_endpoint:
        resource = Resource.create({SERVICE_NAME: settings.observability.otel_service_name})
        provider = TracerProvider(resource=resource, sampler=ParentBased(root=_DropPlumbing()))
        provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(endpoint=settings.observability.otel_endpoint, insecure=True)
            )
        )
        trace.set_tracer_provider(provider)

        # Instrumented, not excluded: the sampler above drops these traces at
        # the root, and their database and cache calls go with them. Excluding
        # the endpoint instead left those calls parentless, and they came back
        # as a trace each - the same noise wearing a different name.
        FastAPIInstrumentor.instrument_app(app)
        HTTPXClientInstrumentor().instrument()
        RedisInstrumentor().instrument()

    if settings.observability.sentry_dsn:
        import sentry_sdk  # noqa: PLC0415  (optional dep, only when SENTRY_DSN is set)

        sentry_sdk.init(
            dsn=settings.observability.sentry_dsn,
            environment=settings.app.env,
            traces_sample_rate=0.1 if settings.is_prod else 1.0,
        )


def instrument_sqlalchemy_engine(engine: object) -> None:
    """Called after engine is created."""
    settings = get_settings()
    if settings.observability.otel_endpoint:
        SQLAlchemyInstrumentor().instrument(engine=engine)
