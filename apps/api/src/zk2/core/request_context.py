"""Per-request logging context.

Every log line from a request carries the same request_id, and the trace_id of
the OpenTelemetry span when tracing is on. That is what turns "an error at
14:03" into "this request, these twenty lines, this trace" - the log line, the
Jaeger trace and the Sentry event all name the same id.

Clients may supply X-Request-Id (a load balancer or gateway usually does); it
is echoed back on the response either way.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import structlog
from opentelemetry import trace as otel_trace
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-Id"

Handler = Callable[[Request], Awaitable[Response]]


def _current_trace_id() -> str | None:
    context = otel_trace.get_current_span().get_span_context()
    if not context.is_valid:
        return None
    return format(context.trace_id, "032x")


async def request_context_middleware(request: Request, call_next: Handler) -> Response:
    structlog.contextvars.clear_contextvars()
    request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
    bindings: dict[str, str] = {
        "request_id": request_id,
        "method": request.method,
        "path": request.url.path,
    }
    trace_id = _current_trace_id()
    if trace_id:
        bindings["trace_id"] = trace_id
    structlog.contextvars.bind_contextvars(**bindings)

    response = await call_next(request)
    response.headers[REQUEST_ID_HEADER] = request_id
    return response
