"""The service switches in the admin panel, and the agent that applies them."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from zk2.auth.models import AuditLog
from zk2.config import get_settings
from zk2.core import tracing
from zk2.ops.models import ManagedService

pytestmark = pytest.mark.integration

AGENT_TOKEN = "agent-token-for-tests"


@pytest.fixture(autouse=True)
def _tracing_switch_restored() -> Iterator[None]:
    yield
    tracing.set_langfuse_switched_off(False)


@pytest.fixture
def agent_token(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    monkeypatch.setattr(get_settings().ops, "agent_token", SecretStr(AGENT_TOKEN))
    return {"X-Ops-Agent-Token": AGENT_TOKEN}


@pytest.fixture
async def admin_client(client: AsyncClient, super_admin: dict[str, Any]) -> AsyncClient:
    resp = await client.post(
        "/auth/login", json={"email": super_admin["email"], "password": super_admin["password"]}
    )
    assert resp.status_code == 200, resp.text
    client.headers["Authorization"] = f"Bearer {resp.json()['access_token']}"
    return client


def _report(state: str, detail: str | None = None) -> dict[str, Any]:
    return {"services": [{"name": "langfuse", "state": state, "detail": detail}]}


async def test_langfuse_starts_out_switched_on(admin_client: AsyncClient) -> None:
    """Before the switch existed it simply ran; the switch must not change that."""
    resp = await admin_client.get("/admin/services")
    assert resp.status_code == 200, resp.text
    (langfuse,) = resp.json()
    assert langfuse["name"] == "langfuse"
    assert langfuse["desired_state"] == "on"
    assert langfuse["observed_state"] is None
    assert langfuse["agent_reporting"] is False


async def test_switching_off_is_audited_and_reaches_the_agent(
    admin_client: AsyncClient,
    db: AsyncSession,
    super_admin: dict[str, Any],
    agent_token: dict[str, str],
) -> None:
    resp = await admin_client.put("/admin/services/langfuse", json={"on": False})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["desired_state"] == "off"
    assert body["changed_by_email"] == super_admin["email"]

    entry = (
        await db.execute(select(AuditLog).where(AuditLog.action == "ops.service_switched_off"))
    ).scalar_one()
    assert entry.actor_user_id == super_admin["id"]
    assert entry.target == "langfuse"

    # The agent still sees the containers running, and is told to stop them
    resp = await admin_client.post(
        "/ops/agent/report", json=_report("running"), headers=agent_token
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"services": [{"name": "langfuse", "desired_state": "off"}]}

    (langfuse,) = (await admin_client.get("/admin/services")).json()
    assert langfuse["observed_state"] == "running"
    assert langfuse["agent_reporting"] is True


async def test_switching_off_stops_this_process_exporting(admin_client: AsyncClient) -> None:
    await admin_client.put("/admin/services/langfuse", json={"on": False})
    assert tracing._langfuse_switched_off is True

    await admin_client.put("/admin/services/langfuse", json={"on": True})
    assert tracing._langfuse_switched_off is False


async def test_no_export_while_the_agent_reports_it_stopped(
    admin_client: AsyncClient, agent_token: dict[str, str]
) -> None:
    """Switched on but not up yet: spans would only be retried into a closed port."""
    await admin_client.post("/ops/agent/report", json=_report("stopped"), headers=agent_token)
    assert tracing._langfuse_switched_off is True

    await admin_client.post("/ops/agent/report", json=_report("running"), headers=agent_token)
    assert tracing._langfuse_switched_off is False


async def test_switched_on_for_a_while_it_switches_itself_off(
    admin_client: AsyncClient, db: AsyncSession
) -> None:
    resp = await admin_client.put(
        "/admin/services/langfuse", json={"on": True, "auto_off_hours": 2}
    )
    off_at = datetime.fromisoformat(resp.json()["off_at"])
    assert timedelta(hours=1, minutes=59) < off_at - datetime.now(UTC) <= timedelta(hours=2)

    await db.execute(
        update(ManagedService)
        .where(ManagedService.name == "langfuse")
        .values(off_at=datetime.now(UTC) - timedelta(minutes=1))
    )
    await db.commit()

    (langfuse,) = (await admin_client.get("/admin/services")).json()
    assert langfuse["desired_state"] == "off"
    assert langfuse["off_at"] is None
    assert langfuse["changed_by_email"] is None

    entry = (
        await db.execute(select(AuditLog).where(AuditLog.action == "ops.service_auto_off"))
    ).scalar_one()
    assert entry.actor_user_id is None


async def test_auto_off_applies_only_to_switching_on(admin_client: AsyncClient) -> None:
    resp = await admin_client.put(
        "/admin/services/langfuse", json={"on": False, "auto_off_hours": 2}
    )
    assert resp.json()["off_at"] is None


async def test_auto_off_is_bounded(admin_client: AsyncClient) -> None:
    for hours in (0, 73):
        resp = await admin_client.put(
            "/admin/services/langfuse", json={"on": True, "auto_off_hours": hours}
        )
        assert resp.status_code == 422, hours


async def test_unknown_service_is_not_found(admin_client: AsyncClient) -> None:
    resp = await admin_client.put("/admin/services/clickhouse", json={"on": False})
    assert resp.status_code == 404


async def test_switches_are_super_admin_only(owner_client: AsyncClient) -> None:
    assert (await owner_client.get("/admin/services")).status_code == 403
    resp = await owner_client.put("/admin/services/langfuse", json={"on": False})
    assert resp.status_code == 403


async def test_agent_endpoint_does_not_exist_without_a_token(client: AsyncClient) -> None:
    resp = await client.post(
        "/ops/agent/report", json=_report("running"), headers={"X-Ops-Agent-Token": "guess"}
    )
    assert resp.status_code == 404


async def test_agent_token_is_checked(client: AsyncClient, agent_token: dict[str, str]) -> None:
    assert (await client.post("/ops/agent/report", json=_report("running"))).status_code == 403
    resp = await client.post(
        "/ops/agent/report", json=_report("running"), headers={"X-Ops-Agent-Token": "wrong"}
    )
    assert resp.status_code == 403


async def test_agent_reports_about_unknown_services_are_ignored(
    client: AsyncClient, agent_token: dict[str, str]
) -> None:
    resp = await client.post(
        "/ops/agent/report",
        json={"services": [{"name": "grafana", "state": "running"}]},
        headers=agent_token,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"services": [{"name": "langfuse", "desired_state": "on"}]}
