"""End-to-end auth flow: access request → super-admin approve → invite accept → login."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from zk2.auth.models import Invite

pytestmark = pytest.mark.integration


async def _bearer(client: AsyncClient, email: str, password: str) -> str:
    resp = await client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


async def test_health(client: AsyncClient) -> None:
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_login_with_bad_credentials(client: AsyncClient) -> None:
    r = await client.post(
        "/auth/login", json={"email": "nobody@example.com", "password": "Wrong-Pass123"}
    )
    assert r.status_code == 401


async def test_super_admin_can_login(client: AsyncClient, super_admin: dict[str, Any]) -> None:
    token = await _bearer(client, super_admin["email"], super_admin["password"])
    r = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["email"] == super_admin["email"]
    assert body["user"]["is_super_admin"] is True


async def test_refresh_rotates_token(client: AsyncClient, super_admin: dict[str, Any]) -> None:
    login = await client.post(
        "/auth/login",
        json={"email": super_admin["email"], "password": super_admin["password"]},
    )
    first_refresh = login.json()["refresh_token"]
    r = await client.post("/auth/refresh", json={"refresh_token": first_refresh})
    assert r.status_code == 200
    new_refresh = r.json()["refresh_token"]
    assert new_refresh != first_refresh

    # Old refresh must now be invalid
    r2 = await client.post("/auth/refresh", json={"refresh_token": first_refresh})
    assert r2.status_code == 401


async def test_access_request_flow(
    client: AsyncClient, db: AsyncSession, super_admin: dict[str, Any]
) -> None:
    # 1. Public: submit access request
    r = await client.post(
        "/access-requests",
        json={"email": "newcomer@example.com", "message": "let me in"},
    )
    assert r.status_code == 201

    # 2. Super-admin: list pending
    token = await _bearer(client, super_admin["email"], super_admin["password"])
    headers = {"Authorization": f"Bearer {token}"}
    r = await client.get("/admin/access-requests?status=pending", headers=headers)
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1
    request_id = items[0]["id"]

    # 3. Super-admin: approve
    r = await client.post(
        f"/admin/access-requests/{request_id}/decide",
        headers=headers,
        json={"approve": True, "create_org_name": "Newcomer Inc"},
    )
    assert r.status_code == 200, r.text

    # 4. Verify invite was created
    invites = (
        (await db.execute(select(Invite).where(Invite.email == "newcomer@example.com")))
        .scalars()
        .all()
    )
    assert len(invites) == 1


async def test_rate_limit_access_request_by_email(client: AsyncClient) -> None:
    # First should pass
    r = await client.post(
        "/access-requests", json={"email": "spammer@example.com", "message": "first"}
    )
    assert r.status_code == 201
    # Second from same email should be rate-limited (default: 1/h)
    r = await client.post(
        "/access-requests", json={"email": "spammer@example.com", "message": "second"}
    )
    assert r.status_code == 429
    assert "Retry-After" in r.headers


async def test_admin_endpoints_require_super_admin(client: AsyncClient, db: AsyncSession) -> None:
    """Non-super-admin user must NOT access /admin/*."""
    from datetime import UTC, datetime

    from zk2.auth.models import User
    from zk2.core.security import hash_password

    user = User(
        email="regular@example.com",
        password_hash=hash_password("RegularPass123!"),
        is_super_admin=False,
        is_active=True,
        email_verified_at=datetime.now(UTC),
    )
    db.add(user)
    await db.commit()

    token = await _bearer(client, "regular@example.com", "RegularPass123!")
    r = await client.get("/admin/access-requests", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


async def test_me_requires_auth(client: AsyncClient) -> None:
    r = await client.get("/auth/me")
    assert r.status_code == 401
