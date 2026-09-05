"""MCP: tools an organization connected from outside this codebase.

Each configured server is asked what it can do, and its tools are wrapped in
the same ToolSpec the built-ins use. Calls open a short-lived session rather
than holding connections: an agent turn is bursty, and a pooled connection to
someone else's server is a liability across restarts and network hiccups.

Failures are contained. A server that is down, slow or lying costs its own
tools for that turn and is marked unhealthy - it does not fail the turn.

Scope note: only unauthenticated servers are supported in this iteration. The
SDK routes auth headers through a low-level transport that cannot be verified
here without a live authenticated server, and shipping an unverified auth path
is worse than not offering one. The encrypted-headers column exists for when it
is implemented; the API rejects headers today rather than storing them and
silently ignoring them.
"""

from __future__ import annotations

import json
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from zk2.agents.models import McpServer
from zk2.agents.tools import ToolSpec, _truncate
from zk2.core.net import assert_url_allowed

logger = structlog.get_logger()

CONNECT_TIMEOUT_SECONDS = 10.0


def _render(result: Any) -> str:
    """Flatten an MCP tool result into text the model can read."""
    if getattr(result, "structuredContent", None):
        return _truncate(json.dumps(result.structuredContent, ensure_ascii=False))
    parts: list[str] = []
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        parts.append(text if text is not None else str(block))
    return _truncate("\n".join(parts) if parts else "(empty result)")


async def list_server_tools(server: McpServer) -> list[ToolSpec]:
    """Ask one server what it offers, as ToolSpecs the agent can use."""
    await assert_url_allowed(server.url)

    from mcp import Client  # noqa: PLC0415

    async with Client(server.url) as client:
        listing = await client.list_tools()

    specs: list[ToolSpec] = []
    for tool in listing.tools:
        specs.append(
            ToolSpec(
                # Namespaced: two servers may both offer a "search"
                name=f"{server.name}__{tool.name}",
                description=tool.description or f"{tool.name} (from {server.name})",
                args_model=dict(tool.input_schema or {"type": "object", "properties": {}}),
                handler=_make_handler(server, tool.name),
                kind="mcp",
            )
        )
    return specs


def _make_handler(server: McpServer, tool_name: str):  # type: ignore[no-untyped-def]
    async def _call(**kwargs: Any) -> str:
        from mcp import Client  # noqa: PLC0415

        try:
            await assert_url_allowed(server.url)
            async with Client(server.url) as client:
                result = await client.call_tool(tool_name, kwargs)
        except Exception as exc:
            logger.warning("mcp.call_failed", server=server.name, tool=tool_name, error=str(exc))
            return f"Tool {tool_name} failed: {str(exc)[:300]}"
        if getattr(result, "isError", None):
            return f"Tool {tool_name} reported an error: {_render(result)}"
        return _render(result)

    return _call


async def mcp_tools_for_org(db: AsyncSession, *, org_id: int) -> list[ToolSpec]:
    """Tools from every enabled server this organization has connected."""
    servers = (
        (
            await db.execute(
                select(McpServer).where(McpServer.org_id == org_id, McpServer.enabled.is_(True))
            )
        )
        .scalars()
        .all()
    )
    specs: list[ToolSpec] = []
    for server in servers:
        try:
            specs.extend(await list_server_tools(server))
        except Exception as exc:
            logger.warning("mcp.listing_failed", server=server.name, error=str(exc)[:200])
    return specs


async def check_server(db: AsyncSession, server: McpServer) -> McpServer:
    """Health check: record whether the server answers and how many tools it has."""
    from datetime import UTC, datetime  # noqa: PLC0415

    try:
        specs = await list_server_tools(server)
    except Exception as exc:
        server.status = "error"
        server.last_error = str(exc)[:1000]
        server.tool_count = None
    else:
        server.status = "ok"
        server.last_error = None
        server.tool_count = len(specs)
    server.last_checked_at = datetime.now(UTC)
    await db.flush()
    return server
