"""Request correlation: one id across logs, traces and the response."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


async def test_request_id_is_generated_and_returned(client: AsyncClient) -> None:
    resp = await client.get("/health")
    assert resp.headers["X-Request-Id"]
    assert len(resp.headers["X-Request-Id"]) == 32


async def test_client_supplied_request_id_is_kept(client: AsyncClient) -> None:
    """A gateway that already assigned an id must stay authoritative."""
    resp = await client.get("/health", headers={"X-Request-Id": "abc-123"})
    assert resp.headers["X-Request-Id"] == "abc-123"


async def test_each_request_gets_its_own_id(client: AsyncClient) -> None:
    first = (await client.get("/health")).headers["X-Request-Id"]
    second = (await client.get("/health")).headers["X-Request-Id"]
    assert first != second
