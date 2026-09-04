"""HTTP metrics middleware.

Labels use the matched route template (`/sources/{source_id}`), not the
resolved path: one time series per endpoint instead of one per document id.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from starlette.requests import Request
from starlette.responses import Response

from zk2.core.metrics import http_request_duration_seconds, http_requests_total

Handler = Callable[[Request], Awaitable[Response]]


async def metrics_middleware(request: Request, call_next: Handler) -> Response:
    started = time.perf_counter()
    response = await call_next(request)
    route = request.scope.get("route")
    # Unmatched paths (404s, scanners) collapse into one series
    template = getattr(route, "path", "unmatched")
    elapsed = time.perf_counter() - started
    http_requests_total.labels(
        method=request.method, route=template, status=str(response.status_code)
    ).inc()
    http_request_duration_seconds.labels(method=request.method, route=template).observe(elapsed)
    return response
