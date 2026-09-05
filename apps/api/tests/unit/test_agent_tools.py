"""Built-in agent tools: what they return, and what they refuse."""

from __future__ import annotations

import json
from typing import Any

import pytest

from zk2.agents.tools import (
    MAX_TOOL_OUTPUT_CHARS,
    _calculator,
    _current_time,
    _fetch_url,
    _sql_query,
    _web_search,
    builtin_tools,
)
from zk2.config import get_settings

pytestmark = pytest.mark.unit


async def test_current_time_returns_iso_in_the_requested_zone() -> None:
    result = await _current_time("Europe/Kyiv")
    assert result[:2] == "20"
    assert "+" in result or "Z" in result


async def test_unknown_timezone_is_explained_not_raised() -> None:
    """A tool error is a message for the model, not an exception in the loop."""
    assert "Unknown timezone" in await _current_time("Mars/Olympus")


async def test_calculator_evaluates_arithmetic() -> None:
    assert float(await _calculator("(1200 * 1.2) / 3")) == pytest.approx(480.0)


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os').system('rm -rf /')",
        "open('/etc/passwd').read()",
        "factorial(99999)",
        "x + y",
    ],
)
async def test_calculator_refuses_anything_but_arithmetic(expression: str) -> None:
    assert "Only arithmetic" in await _calculator(expression)


async def test_fetch_url_refuses_private_addresses(monkeypatch: pytest.MonkeyPatch) -> None:
    """The agent gets the same SSRF guard as user-supplied source URLs."""
    result = await _fetch_url("http://169.254.169.254/latest/meta-data/")
    assert "Refused" in result


async def test_fetch_url_returns_readable_text(monkeypatch: pytest.MonkeyPatch) -> None:
    from zk2.sources.web import FetchedPage

    async def _fake(*_args: object, **_kwargs: object) -> FetchedPage:
        return FetchedPage(
            url="https://example.com",
            final_url="https://example.com",
            title="Doc",
            text="Body text",
            content_type="text/html",
        )

    monkeypatch.setattr("zk2.agents.tools.fetch_url", _fake)
    assert await _fetch_url("https://example.com") == "Doc\n\nBody text"


async def test_long_tool_output_is_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    from zk2.sources.web import FetchedPage

    async def _fake(*_args: object, **_kwargs: object) -> FetchedPage:
        return FetchedPage(
            url="https://example.com",
            final_url="https://example.com",
            title="Doc",
            text="x" * (MAX_TOOL_OUTPUT_CHARS * 2),
            content_type="text/html",
        )

    monkeypatch.setattr("zk2.agents.tools.fetch_url", _fake)
    result = await _fetch_url("https://example.com")
    assert result.endswith("[truncated]")
    assert len(result) < MAX_TOOL_OUTPUT_CHARS + 100


async def test_web_search_without_a_key_says_so() -> None:
    assert "not configured" in await _web_search("anything")


async def test_web_search_formats_results(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx
    from pydantic import SecretStr

    monkeypatch.setattr(get_settings().tools, "brave_search_api_key", SecretStr("test-key"))

    payload = {
        "web": {
            "results": [
                {"title": "First", "url": "https://a.example", "description": "A"},
                {"title": "Second", "url": "https://b.example", "description": "B"},
            ]
        }
    }
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["token"] = request.headers.get("x-subscription-token")
        return httpx.Response(200, json=payload)

    real_client = httpx.AsyncClient

    def _factory(**kwargs: object) -> httpx.AsyncClient:
        kwargs.pop("timeout", None)
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr("zk2.agents.tools.httpx.AsyncClient", _factory)
    result = await _web_search("query", count=2)
    assert "First" in result and "https://b.example" in result
    assert captured["token"] == "test-key"


@pytest.mark.parametrize(
    "query",
    [
        "DELETE FROM users",
        "select 1; drop table users",
        "update sources set name = 'x'",
        "INSERT INTO users VALUES (1)",
        "TRUNCATE audit_log",
    ],
)
async def test_sql_tool_refuses_anything_that_writes(query: str) -> None:
    result = await _sql_query(query)
    assert "allowed" in result or "One statement" in result


async def test_sql_tool_is_absent_without_a_readonly_dsn() -> None:
    """No sandbox configured means the tool is not offered at all."""
    assert "sql_query" not in {tool.name for tool in builtin_tools()}


async def test_registry_offers_the_always_available_tools() -> None:
    names = {tool.name for tool in builtin_tools()}
    assert {"get_current_time", "calculator", "fetch_url"} <= names


async def test_every_tool_has_a_description_and_schema() -> None:
    for tool in builtin_tools():
        assert tool.description
        schema = tool.args_model.model_json_schema()
        assert json.dumps(schema)  # serialisable: it goes to the provider
