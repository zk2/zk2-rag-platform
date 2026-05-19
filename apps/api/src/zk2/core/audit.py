"""Audit log helper."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from zk2.auth.models import AuditLog


async def write_audit(
    db: AsyncSession,
    *,
    action: str,
    actor_user_id: int | None = None,
    org_id: int | None = None,
    target: str | None = None,
    payload: dict[str, Any] | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> None:
    entry = AuditLog(
        actor_user_id=actor_user_id,
        org_id=org_id,
        action=action,
        target=target,
        payload=payload or {},
        ip=ip,
        user_agent=user_agent,
    )
    db.add(entry)
    await db.flush()
