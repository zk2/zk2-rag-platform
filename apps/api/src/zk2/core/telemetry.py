"""OpenTelemetry + Sentry setup."""

from __future__ import annotations

from typing import TYPE_CHECKING

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from zk2.config import get_settings

if TYPE_CHECKING:
    from fastapi import FastAPI


def setup_telemetry(app: FastAPI) -> None:
    settings = get_settings()

    if settings.observability.otel_endpoint:
        resource = Resource.create({SERVICE_NAME: settings.observability.otel_service_name})
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(endpoint=settings.observability.otel_endpoint, insecure=True)
            )
        )
        trace.set_tracer_provider(provider)

        FastAPIInstrumentor.instrument_app(app)
        HTTPXClientInstrumentor().instrument()
        RedisInstrumentor().instrument()

    if settings.observability.sentry_dsn:
        import sentry_sdk
        from sentry_sdk.integrations.asgi import SentryAsgiMiddleware  # noqa: F401

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
