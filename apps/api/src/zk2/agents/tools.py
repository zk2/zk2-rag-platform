"""Built-in agent tools.

Tools are described in framework-neutral terms - a name, a description, a
pydantic argument model and an async handler - and adapted to LangChain only
where the graph is built. MCP tools arrive in the same shape, so the agent code
never asks where a tool came from.

Availability is a property of the deployment: a tool whose dependency is not
configured (no search key, no read-only database) is not registered at all,
rather than registered and failing when the model finally reaches for it.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
import structlog
from pydantic import BaseModel, Field

from zk2.config import get_settings
from zk2.core.net import DEFAULT_USER_AGENT, UnsafeUrlError
from zk2.sources.web import fetch_url

logger = structlog.get_logger()

MAX_TOOL_OUTPUT_CHARS = 8000
BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """One tool the agent may call."""

    name: str
    description: str
    # A pydantic model for built-ins; MCP servers hand us raw JSON Schema
    args_model: type[BaseModel] | dict[str, Any]
    handler: Callable[..., Awaitable[str]]
    kind: str = "builtin"


def _truncate(text: str) -> str:
    if len(text) <= MAX_TOOL_OUTPUT_CHARS:
        return text
    return text[:MAX_TOOL_OUTPUT_CHARS] + "\n... [truncated]"


# ─── time ─────────────────────────────────────────────────────────────


class TimeArgs(BaseModel):
    timezone: str = Field("UTC", description="IANA timezone name, e.g. Europe/Kyiv")


async def _current_time(timezone: str = "UTC") -> str:
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        return f"Unknown timezone: {timezone}. Use an IANA name such as Europe/Kyiv."
    return datetime.now(zone).isoformat()


# ─── calculator ───────────────────────────────────────────────────────


class CalculatorArgs(BaseModel):
    expression: str = Field(..., description="Arithmetic expression, e.g. (1200 * 1.2) / 3")


# sympy will evaluate anything sympy understands, so the input is filtered to a
# numeric grammar first - the model is not a trusted source of expressions.
_ARITHMETIC_ONLY = re.compile(r"^[0-9\s+\-*/().,%^eE]+$")


async def _calculator(expression: str) -> str:
    """Evaluate arithmetic exactly, with sympy doing the arithmetic."""
    if not _ARITHMETIC_ONLY.match(expression):
        return "Only arithmetic is supported: digits, + - * / ( ) . % ^"
    from sympy import sympify  # noqa: PLC0415  (heavy import, only when used)

    try:
        value = sympify(expression.replace("^", "**"), evaluate=True)
        return str(value.evalf())
    except Exception as exc:
        return f"Could not evaluate: {exc}"


# ─── fetch_url ────────────────────────────────────────────────────────


class FetchArgs(BaseModel):
    url: str = Field(..., description="Absolute http(s) URL to read")


async def _fetch_url(url: str) -> str:
    """Read a web page. Same SSRF guard as user-supplied source URLs."""
    try:
        page = await fetch_url(url)
    except UnsafeUrlError as exc:
        return f"Refused: {exc}"
    except httpx.HTTPError as exc:
        return f"Could not fetch: {exc}"
    return _truncate(f"{page.title}\n\n{page.text}")


# ─── web_search ───────────────────────────────────────────────────────


class SearchArgs(BaseModel):
    query: str = Field(..., description="Search query")
    count: int = Field(5, ge=1, le=10, description="Number of results")


async def _web_search(query: str, count: int = 5) -> str:
    settings = get_settings().tools
    key = settings.brave_search_api_key
    if key is None:
        return "Web search is not configured for this deployment."
    headers = {
        "X-Subscription-Token": key.get_secret_value(),
        "Accept": "application/json",
        "User-Agent": DEFAULT_USER_AGENT,
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            BRAVE_ENDPOINT, params={"q": query, "count": count}, headers=headers
        )
        if response.status_code != 200:
            logger.warning("tools.search_failed", status=response.status_code)
            return f"Search failed with status {response.status_code}"
        payload: dict[str, Any] = response.json()

    results = payload.get("web", {}).get("results", [])[:count]
    if not results:
        return "No results."
    lines = [
        f"{index}. {item.get('title', 'untitled')}\n   {item.get('url', '')}\n"
        f"   {item.get('description', '')}"
        for index, item in enumerate(results, start=1)
    ]
    return _truncate("\n".join(lines))


# ─── read-only SQL ────────────────────────────────────────────────────


class SqlArgs(BaseModel):
    query: str = Field(..., description="A single read-only SELECT statement")


_FORBIDDEN_SQL = re.compile(
    r"\b(insert|update|delete|drop|alter|create|grant|revoke|truncate|copy|call|do)\b",
    re.IGNORECASE,
)


async def _sql_query(query: str) -> str:
    """Run one SELECT against the analytics database.

    Three independent guards, because one is never enough for a query written
    by a language model: the statement must be a single SELECT, it runs on a
    connection whose database user has no write grants, and it is wrapped in a
    read-only transaction with a statement timeout.
    """
    from zk2.agents.sql_sandbox import run_readonly_query  # noqa: PLC0415  (optional feature)

    statement = query.strip().rstrip(";")
    if ";" in statement:
        return "One statement at a time, please."
    if not statement.lower().startswith("select") and not statement.lower().startswith("with"):
        return "Only SELECT statements are allowed."
    if _FORBIDDEN_SQL.search(statement):
        return "Only read-only statements are allowed."
    return await run_readonly_query(statement)


# ─── registry ─────────────────────────────────────────────────────────


def builtin_tools() -> list[ToolSpec]:
    """The tools this deployment can actually run."""
    settings = get_settings()
    specs = [
        ToolSpec(
            name="get_current_time",
            description="Current date and time in a given IANA timezone.",
            args_model=TimeArgs,
            handler=_current_time,
        ),
        ToolSpec(
            name="calculator",
            description="Evaluate an arithmetic expression exactly.",
            args_model=CalculatorArgs,
            handler=_calculator,
        ),
        ToolSpec(
            name="fetch_url",
            description="Fetch a web page and return its readable text.",
            args_model=FetchArgs,
            handler=_fetch_url,
        ),
    ]
    if settings.tools.brave_search_api_key is not None:
        specs.append(
            ToolSpec(
                name="web_search",
                description="Search the web and return titles, URLs and snippets.",
                args_model=SearchArgs,
                handler=_web_search,
            )
        )
    if settings.tools.readonly_database_url is not None:
        specs.append(
            ToolSpec(
                name="sql_query",
                description=(
                    "Run one read-only SELECT against the analytics database and return the rows."
                ),
                args_model=SqlArgs,
                handler=_sql_query,
            )
        )
    return specs
