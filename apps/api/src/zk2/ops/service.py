"""Service switches: the state a super-admin asked for, and the agent that applies it.

The API never touches Docker. Access to the Docker daemon is root on the host,
and the API is the process facing the internet. So the API only keeps the
state somebody asked for; an agent on the host - `scripts/ops-agent.py`, run by
systemd - reports what is actually running and gets that state back in the
same call, then runs docker compose to close the gap.

Only Langfuse is managed today. It is six containers and about 2 GB of memory
together, and it is needed only while somebody is reading traces.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from zk2.auth.models import User
from zk2.core.audit import write_audit
from zk2.core.db import db_session
from zk2.core.tracing import langfuse_configured, set_langfuse_switched_off
from zk2.ops.models import ManagedService
from zk2.ops.schemas import ServiceReport

logger = structlog.get_logger()

LANGFUSE = "langfuse"
KNOWN_SERVICES: tuple[str, ...] = (LANGFUSE,)

# The agent reports every few seconds. A minute of silence means it is not
# running, and a switch flipped now would change nothing on the host.
AGENT_STALE_AFTER = timedelta(seconds=60)

_FOLLOW_INTERVAL_SECONDS = 15.0


async def _ensure_rows(db: AsyncSession) -> None:
    """Every known service has a row, switched on - which is how they ran before."""
    await db.execute(
        insert(ManagedService)
        .values([{"name": name} for name in KNOWN_SERVICES])
        .on_conflict_do_nothing(index_elements=["name"])
    )


async def _expire_auto_off(db: AsyncSession) -> None:
    """Switch off whatever was switched on "for N hours" and has run out of them.

    One conditional UPDATE, so the api and the worker checking at the same
    moment cannot both claim it and write the audit entry twice.
    """
    expired = (
        (
            await db.execute(
                update(ManagedService)
                .where(
                    ManagedService.desired_state == "on",
                    ManagedService.off_at.is_not(None),
                    ManagedService.off_at <= func.now(),
                )
                .values(
                    desired_state="off",
                    off_at=None,
                    changed_by_user_id=None,
                    changed_at=func.now(),
                )
                .returning(ManagedService.name)
                .execution_options(synchronize_session=False)
            )
        )
        .scalars()
        .all()
    )
    for name in expired:
        await write_audit(db, action="ops.service_auto_off", target=name)
        logger.info("ops.service_auto_off", service=name)


async def load_services(db: AsyncSession) -> list[ManagedService]:
    await _ensure_rows(db)
    await _expire_auto_off(db)
    rows = await db.execute(
        select(ManagedService)
        .order_by(ManagedService.name)
        .execution_options(populate_existing=True)
    )
    return list(rows.scalars().all())


def traces_wanted(service: ManagedService) -> bool:
    """Whether Langfuse should be receiving traces right now.

    Switched off: no, even while the containers are still stopping. Switched
    on but reported stopped: not yet, the spans would only be retried into a
    closed port. Never reported at all - a development machine with no agent -
    counts as running, which is what it was before the switch existed.
    """
    return service.desired_state == "on" and service.observed_state != "stopped"


def _sync_tracing(services: Sequence[ManagedService]) -> None:
    for service in services:
        if service.name == LANGFUSE:
            set_langfuse_switched_off(not traces_wanted(service))


async def switch(
    db: AsyncSession,
    *,
    name: str,
    on: bool,
    auto_off_hours: int | None,
    actor: User,
    ip: str | None,
    user_agent: str | None,
) -> ManagedService:
    """Record a super-admin's switch. The caller has checked that `name` is known."""
    service = next(s for s in await load_services(db) if s.name == name)
    now = datetime.now(UTC)
    service.desired_state = "on" if on else "off"
    service.off_at = now + timedelta(hours=auto_off_hours) if on and auto_off_hours else None
    service.changed_by_user_id = actor.id
    service.changed_at = now
    await write_audit(
        db,
        action="ops.service_switched_on" if on else "ops.service_switched_off",
        actor_user_id=actor.id,
        target=name,
        payload={"auto_off_hours": auto_off_hours if on else None},
        ip=ip,
        user_agent=user_agent,
    )
    await db.flush()
    logger.info("ops.service_switched", service=name, on=on, auto_off_hours=auto_off_hours)
    _sync_tracing([service])
    return service


async def record_reports(
    db: AsyncSession, reports: Sequence[ServiceReport]
) -> list[ManagedService]:
    """Store what the agent sees; return every service with the state it should be in."""
    services = await load_services(db)
    by_name = {s.name: s for s in services}
    now = datetime.now(UTC)
    for report in reports:
        service = by_name.get(report.name)
        if service is None:
            continue
        if service.observed_state != report.state:
            logger.info(
                "ops.service_observed",
                service=report.name,
                state=report.state,
                previous=service.observed_state,
                detail=report.detail,
            )
        service.observed_state = report.state
        service.observed_detail = report.detail[:512] if report.detail else None
        service.observed_at = now
    await db.flush()
    _sync_tracing(services)
    return services


async def follow_langfuse_switch(interval: float = _FOLLOW_INTERVAL_SECONDS) -> None:
    """Keep this process's Langfuse export in step with the switch.

    Runs in the api and in the worker, both of which trace. A super-admin's
    click lands in one api process; everything else learns of it here, within
    one interval. It is also where "on for N hours" runs out when nobody has the
    admin page open.
    """
    if not langfuse_configured():
        return
    while True:
        try:
            async with db_session() as db:
                services = await load_services(db)
            _sync_tracing(services)
        except Exception:
            # Keep the last known state: a database blip is no reason to start
            # or stop exporting
            logger.warning("ops.langfuse_switch_unreadable", exc_info=True)
        await asyncio.sleep(interval)
