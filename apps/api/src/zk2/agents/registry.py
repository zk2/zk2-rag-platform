"""What tools an organization's agent can reach.

Built-ins come from the deployment; MCP tools come from the servers the
organization connected. Both arrive as ToolSpec, so the agent code cannot tell
them apart - which is the point of MCP.

A failing MCP server degrades to "its tools are missing this turn" rather than
failing the turn: an agent with fewer tools can still answer, an agent that
raised cannot.
"""

from __future__ import annotations

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from zk2.agents.tools import ToolSpec, builtin_tools

logger = structlog.get_logger()


async def tools_for_org(
    db: AsyncSession, *, org_id: int, names: list[str] | None = None
) -> list[ToolSpec]:
    """Every tool available to this organization, optionally filtered by name."""
    from zk2.agents.mcp_client import mcp_tools_for_org  # noqa: PLC0415  (optional feature)

    specs = list(builtin_tools())
    try:
        specs.extend(await mcp_tools_for_org(db, org_id=org_id))
    except Exception:
        logger.exception("agents.mcp_tools_failed", org_id=org_id)

    if names:
        wanted = set(names)
        specs = [spec for spec in specs if spec.name in wanted]
    return specs
