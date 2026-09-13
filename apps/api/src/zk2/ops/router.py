"""Service switches: the super-admin's side, and the host agent's."""

from __future__ import annotations

import secrets
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from zk2.auth.models import User
from zk2.auth.rbac import require_super_admin
from zk2.config import get_settings
from zk2.core.deps import get_client_ip, get_db_dep, get_user_agent
from zk2.core.errors import ForbiddenError, NotFoundError
from zk2.ops.models import ManagedService
from zk2.ops.schemas import (
    AgentInstructions,
    AgentReport,
    DesiredState,
    ManagedServiceDto,
    ServiceSwitch,
)
from zk2.ops.service import AGENT_STALE_AFTER, KNOWN_SERVICES, load_services, record_reports, switch

admin_router = APIRouter(
    prefix="/admin/services",
    tags=["admin"],
    dependencies=[Depends(require_super_admin())],
)

# Not in the schema, and answered 404 by Caddy from outside: the only caller is
# the agent, which reaches the api over the compose network.
agent_router = APIRouter(prefix="/ops/agent", tags=["ops"], include_in_schema=False)


async def _dtos(db: AsyncSession, services: Sequence[ManagedService]) -> list[ManagedServiceDto]:
    ids = {s.changed_by_user_id for s in services if s.changed_by_user_id is not None}
    emails: dict[int, str] = {}
    if ids:
        rows = await db.execute(select(User.id, User.email).where(User.id.in_(ids)))
        emails = dict(rows.tuples().all())
    now = datetime.now(UTC)
    return [
        ManagedServiceDto.model_validate(
            {
                "name": s.name,
                "desired_state": s.desired_state,
                "off_at": s.off_at,
                "observed_state": s.observed_state,
                "observed_detail": s.observed_detail,
                "observed_at": s.observed_at,
                "agent_reporting": s.observed_at is not None
                and now - s.observed_at < AGENT_STALE_AFTER,
                "changed_at": s.changed_at,
                "changed_by_email": emails.get(s.changed_by_user_id)
                if s.changed_by_user_id is not None
                else None,
            }
        )
        for s in services
    ]


@admin_router.get("", response_model=list[ManagedServiceDto])
async def list_services(
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> list[ManagedServiceDto]:
    return await _dtos(db, await load_services(db))


@admin_router.put("/{name}", response_model=ManagedServiceDto)
async def switch_service(
    name: str,
    payload: ServiceSwitch,
    request: Request,
    user: Annotated[User, Depends(require_super_admin())],
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> ManagedServiceDto:
    if name not in KNOWN_SERVICES:
        raise NotFoundError("No such managed service")
    service = await switch(
        db,
        name=name,
        on=payload.on,
        auto_off_hours=payload.auto_off_hours,
        actor=user,
        ip=get_client_ip(request),
        user_agent=get_user_agent(request),
    )
    (dto,) = await _dtos(db, [service])
    return dto


async def _require_agent(
    token: Annotated[str | None, Header(alias="X-Ops-Agent-Token")] = None,
) -> None:
    expected = get_settings().ops.agent_token
    if expected is None:
        # No token configured means no agent: the endpoint does not exist
        raise NotFoundError("Not found")
    if token is None or not secrets.compare_digest(
        token.encode(), expected.get_secret_value().encode()
    ):
        raise ForbiddenError("Invalid agent token")


@agent_router.post(
    "/report", response_model=AgentInstructions, dependencies=[Depends(_require_agent)]
)
async def agent_report(
    payload: AgentReport,
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> AgentInstructions:
    """What the agent sees on the host, answered with what it should make true."""
    services = await record_reports(db, payload.services)
    return AgentInstructions(
        services=[
            DesiredState.model_validate({"name": s.name, "desired_state": s.desired_state})
            for s in services
        ]
    )
