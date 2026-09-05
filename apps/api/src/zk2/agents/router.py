"""Agent tooling API: what tools exist, and which MCP servers are connected."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from zk2.agents.models import McpServer
from zk2.agents.registry import tools_for_org
from zk2.agents.schemas import McpServerCreate, McpServerDto, McpServerPatch, ToolDto
from zk2.auth.rbac import OrgContext, require_org
from zk2.core.audit import write_audit
from zk2.core.deps import get_db_dep
from zk2.core.errors import NotFoundError

router = APIRouter(tags=["agents"])


def _schema_of(args_model: object) -> dict:  # type: ignore[type-arg]
    if isinstance(args_model, dict):
        return args_model
    model = args_model
    return (
        model.model_json_schema()
        if isinstance(model, type) and issubclass(model, BaseModel)
        else {}
    )


@router.get("/agents/tools", response_model=list[ToolDto])
async def list_tools(
    ctx: Annotated[OrgContext, Depends(require_org("viewer"))],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> list[ToolDto]:
    """Every tool this organization's agents can call, built-in and MCP."""
    specs = await tools_for_org(db, org_id=ctx.org_id)
    return [
        ToolDto(
            name=spec.name,
            description=spec.description,
            kind=spec.kind,
            args_schema=_schema_of(spec.args_model),
        )
        for spec in specs
    ]


@router.get("/settings/mcp", response_model=list[McpServerDto])
async def list_servers(
    ctx: Annotated[OrgContext, Depends(require_org("viewer"))],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> list[McpServerDto]:
    rows = (
        (
            await db.execute(
                select(McpServer)
                .where(McpServer.org_id == ctx.org_id)
                .order_by(McpServer.created_at)
            )
        )
        .scalars()
        .all()
    )
    return [McpServerDto.model_validate(row) for row in rows]


@router.post("/settings/mcp", response_model=McpServerDto, status_code=status.HTTP_201_CREATED)
async def add_server(
    payload: McpServerCreate,
    ctx: Annotated[OrgContext, Depends(require_org("admin"))],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> McpServerDto:
    """Connect a server. It is checked immediately, so a typo surfaces now."""
    from zk2.agents.mcp_client import check_server  # noqa: PLC0415

    server = McpServer(
        org_id=ctx.org_id, name=payload.name, url=str(payload.url), enabled=payload.enabled
    )
    db.add(server)
    await db.flush()
    await check_server(db, server)
    await write_audit(
        db,
        action="mcp.server.added",
        actor_user_id=ctx.user.id,
        org_id=ctx.org_id,
        target=payload.name,
        payload={"url": str(payload.url), "status": server.status},
    )
    return McpServerDto.model_validate(server)


@router.patch("/settings/mcp/{server_id}", response_model=McpServerDto)
async def patch_server(
    server_id: int,
    payload: McpServerPatch,
    ctx: Annotated[OrgContext, Depends(require_org("admin"))],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> McpServerDto:
    server = await _require_server(db, org_id=ctx.org_id, server_id=server_id)
    if payload.name is not None:
        server.name = payload.name
    if payload.url is not None:
        server.url = str(payload.url)
    if payload.enabled is not None:
        server.enabled = payload.enabled
    await db.flush()
    return McpServerDto.model_validate(server)


@router.post("/settings/mcp/{server_id}/check", response_model=McpServerDto)
async def check_one(
    server_id: int,
    ctx: Annotated[OrgContext, Depends(require_org("editor"))],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> McpServerDto:
    """Re-run the health check now instead of waiting for the periodic job."""
    from zk2.agents.mcp_client import check_server  # noqa: PLC0415

    server = await _require_server(db, org_id=ctx.org_id, server_id=server_id)
    await check_server(db, server)
    return McpServerDto.model_validate(server)


@router.delete("/settings/mcp/{server_id}")
async def delete_server(
    server_id: int,
    ctx: Annotated[OrgContext, Depends(require_org("admin"))],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> dict[str, str]:
    server = await _require_server(db, org_id=ctx.org_id, server_id=server_id)
    await db.delete(server)
    await db.flush()
    await write_audit(
        db,
        action="mcp.server.removed",
        actor_user_id=ctx.user.id,
        org_id=ctx.org_id,
        target=server.name,
    )
    return {"status": "deleted"}


async def _require_server(db: AsyncSession, *, org_id: int, server_id: int) -> McpServer:
    server = await db.scalar(
        select(McpServer).where(McpServer.id == server_id, McpServer.org_id == org_id)
    )
    if server is None:
        raise NotFoundError("MCP server not found")
    return server
