"""Tracing for a chat turn: OpenTelemetry spans plus Langfuse observations.

Two audiences, one call path. OTel answers "where did the latency go" for the
whole request, including database and HTTP work, and lands in Jaeger. Langfuse
answers "what did the model actually see and produce" - prompt, retrieved
passages, tokens, cost - which is what you need when an answer is wrong rather
than slow.

Both are optional. With no OTLP endpoint and no Langfuse keys, every call here
is a no-op object: the chat path must not depend on observability being up.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import structlog
from opentelemetry import trace as otel_trace
from opentelemetry.trace import Span

from zk2.config import get_settings

logger = structlog.get_logger()

_TRACER_NAME = "zk2.rag"


@lru_cache(maxsize=1)
def get_langfuse() -> Any | None:
    """Langfuse client, or None when the deployment has no keys configured."""
    settings = get_settings().observability
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        return None
    try:
        from langfuse import Langfuse  # noqa: PLC0415  (optional at runtime)

        client = Langfuse(
            public_key=settings.langfuse_public_key.get_secret_value(),
            secret_key=settings.langfuse_secret_key.get_secret_value(),
            host=settings.langfuse_host,
            environment=get_settings().app.env,
        )
        logger.info("tracing.langfuse_enabled", host=settings.langfuse_host)
        return client
    except Exception:
        logger.exception("tracing.langfuse_init_failed")
        return None


@dataclass(slots=True)
class TraceStep:
    """One step of a turn - a pipeline node, or the model call inside one."""

    otel_span: Span | None = None
    langfuse_observation: Any | None = None

    def child(
        self,
        name: str,
        *,
        kind: str = "span",
        input_data: Any = None,
        model: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TraceStep:
        """A step nested inside this one.

        Created from the parent objects rather than from the client, because
        neither backend infers the nesting on its own: an observation started
        from the client attaches to whatever OTel context happens to be
        current, which for a chat turn is the websocket connection. Every span
        then arrives as a root of its own - one turn showed up in Langfuse as
        eight unrelated traces instead of one tree.
        """
        otel_span = None
        tracer = otel_trace.get_tracer(_TRACER_NAME)
        if self.otel_span is not None:
            parent = otel_trace.set_span_in_context(self.otel_span)
            otel_span = tracer.start_span(name, context=parent)
        else:
            otel_span = tracer.start_span(name)

        observation = None
        if self.langfuse_observation is not None:
            try:
                observation = self.langfuse_observation.start_observation(
                    name=name, as_type=kind, input=input_data, model=model, metadata=metadata
                )
            except Exception:
                logger.warning("tracing.step_start_failed", step=name)
        return TraceStep(otel_span=otel_span, langfuse_observation=observation)

    def end(self, **fields: Any) -> None:
        if self.langfuse_observation is not None:
            try:
                self.langfuse_observation.update(**fields)
                self.langfuse_observation.end()
            except Exception:
                logger.warning("tracing.step_end_failed")
        if self.otel_span is not None:
            for key, value in fields.items():
                if isinstance(value, str | int | float | bool):
                    self.otel_span.set_attribute(f"zk2.{key}", value)
            self.otel_span.end()


@dataclass(slots=True)
class TurnTrace:
    """Root of one chat turn. Every method is safe when tracing is disabled."""

    root: TraceStep = field(default_factory=TraceStep)
    trace_id: str | None = None
    trace_url: str | None = None
    _client: Any | None = None

    def step(
        self,
        name: str,
        *,
        kind: str = "span",
        input_data: Any = None,
        model: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TraceStep:
        """A step directly under the turn."""
        return self.root.child(
            name, kind=kind, input_data=input_data, model=model, metadata=metadata
        )

    def end(self, **fields: Any) -> None:
        self.root.end(**fields)


def start_turn(name: str, *, metadata: dict[str, Any] | None = None) -> TurnTrace:
    """Begin a traced chat turn."""
    tracer = otel_trace.get_tracer(_TRACER_NAME)
    otel_span = tracer.start_span(name)
    for key, value in (metadata or {}).items():
        if isinstance(value, str | int | float | bool):
            otel_span.set_attribute(f"zk2.{key}", value)

    client = get_langfuse()
    observation = None
    trace_id = None
    trace_url = None
    if client is not None:
        try:
            observation = client.start_observation(name=name, as_type="span", metadata=metadata)
            trace_id = client.get_current_trace_id() or observation.trace_id
            trace_url = client.get_trace_url(trace_id=trace_id) if trace_id else None
        except Exception:
            logger.exception("tracing.turn_start_failed")

    return TurnTrace(
        root=TraceStep(otel_span=otel_span, langfuse_observation=observation),
        trace_id=trace_id,
        trace_url=trace_url,
        _client=client,
    )


async def flush_traces() -> None:
    """Send anything buffered. Called on shutdown; safe when disabled."""
    client = get_langfuse()
    if client is None:
        return
    try:
        client.flush()
    except Exception:
        logger.warning("tracing.flush_failed")
