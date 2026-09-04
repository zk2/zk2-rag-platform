"""What the request-context middleware binds for the duration of a request."""

from __future__ import annotations

from typing import Any

import pytest
import structlog
from starlette.requests import Request
from starlette.responses import Response

from zk2.core.request_context import request_context_middleware

pytestmark = pytest.mark.unit


def _request(headers: dict[str, str] | None = None, path: str = "/sources/tree") -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": raw,
        }
    )


async def test_context_is_bound_while_the_handler_runs() -> None:
    seen: dict[str, Any] = {}

    async def handler(_request: Request) -> Response:
        seen.update(structlog.contextvars.get_contextvars())
        return Response("ok")

    await request_context_middleware(_request({"X-Request-Id": "abc-123"}), handler)

    assert seen["request_id"] == "abc-123"
    assert seen["method"] == "GET"
    assert seen["path"] == "/sources/tree"


async def test_trace_id_is_absent_without_tracing() -> None:
    """No OTel provider configured: no trace_id rather than a zeroed one."""
    seen: dict[str, Any] = {}

    async def handler(_request: Request) -> Response:
        seen.update(structlog.contextvars.get_contextvars())
        return Response("ok")

    await request_context_middleware(_request(), handler)
    assert "trace_id" not in seen


async def test_context_does_not_leak_between_requests() -> None:
    async def handler(_request: Request) -> Response:
        return Response("ok")

    await request_context_middleware(_request({"X-Request-Id": "first"}), handler)

    seen: dict[str, Any] = {}

    async def second_handler(_request: Request) -> Response:
        seen.update(structlog.contextvars.get_contextvars())
        return Response("ok")

    await request_context_middleware(_request(), second_handler)
    assert seen["request_id"] != "first"
