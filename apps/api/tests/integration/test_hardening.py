"""Security headers, the global ceiling, and lockout after repeated failures."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from zk2.config import get_settings
from zk2.core.redis_client import get_redis

pytestmark = pytest.mark.integration


async def test_api_responses_carry_security_headers(client: AsyncClient) -> None:
    resp = await client.get("/health")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["Referrer-Policy"] == "no-referrer"
    assert "camera=()" in resp.headers["Permissions-Policy"]


async def test_the_api_content_policy_allows_nothing(client: AsyncClient) -> None:
    """A JSON API needs to load exactly nothing."""
    csp = (await client.get("/health")).headers["Content-Security-Policy"]
    assert "default-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp


async def test_the_docs_get_their_own_policy(client: AsyncClient) -> None:
    """Swagger loads its own assets, so it cannot live under default-src 'none'."""
    csp = (await client.get("/docs")).headers["Content-Security-Policy"]
    assert "script-src" in csp
    assert "frame-ancestors 'none'" in csp


async def test_hsts_only_in_production(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert "Strict-Transport-Security" not in (await client.get("/health")).headers
    monkeypatch.setattr(get_settings().app, "env", "prod")
    assert "Strict-Transport-Security" in (await client.get("/health")).headers


async def test_the_global_ceiling_rejects_a_flood(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings().auth, "rate_limit_global_per_min", 3)
    await get_redis().flushdb()

    statuses = [(await client.get("/auth/me")).status_code for _ in range(6)]
    assert 429 in statuses
    limited = next(s for s in statuses if s == 429)
    assert limited == 429


async def test_health_and_metrics_are_exempt_from_the_ceiling(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A scrape on a schedule must not be able to lock the deployment out."""
    monkeypatch.setattr(get_settings().auth, "rate_limit_global_per_min", 2)
    await get_redis().flushdb()

    for _ in range(10):
        assert (await client.get("/health")).status_code == 200
    assert (await client.get("/metrics")).status_code == 200


async def test_repeated_bad_passwords_lock_the_account(
    client: AsyncClient, super_admin: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings().auth, "lockout_threshold", 3)
    monkeypatch.setattr(get_settings().auth, "rate_limit_login_per_min", 100)
    await get_redis().flushdb()

    for _ in range(3):
        bad = await client.post(
            "/auth/login", json={"email": super_admin["email"], "password": "Wrong-Pass123"}
        )
        assert bad.status_code == 401

    locked = await client.post(
        "/auth/login", json={"email": super_admin["email"], "password": super_admin["password"]}
    )
    assert locked.status_code == 429
    assert "Too many failed attempts" in locked.json()["error"]["message"]
    assert locked.headers.get("Retry-After") or locked.json()["error"]


async def test_a_successful_login_clears_the_counter(
    client: AsyncClient, super_admin: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two typos then the right password must not count towards a lockout."""
    monkeypatch.setattr(get_settings().auth, "lockout_threshold", 3)
    monkeypatch.setattr(get_settings().auth, "rate_limit_login_per_min", 100)
    await get_redis().flushdb()

    for _ in range(2):
        await client.post(
            "/auth/login", json={"email": super_admin["email"], "password": "Wrong-Pass123"}
        )
    ok = await client.post(
        "/auth/login", json={"email": super_admin["email"], "password": super_admin["password"]}
    )
    assert ok.status_code == 200

    for _ in range(2):
        assert (
            await client.post(
                "/auth/login", json={"email": super_admin["email"], "password": "Wrong-Pass123"}
            )
        ).status_code == 401


async def test_an_unknown_email_also_counts_towards_the_address_lockout(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Otherwise a spray across many accounts from one address is free."""
    monkeypatch.setattr(get_settings().auth, "lockout_threshold", 3)
    monkeypatch.setattr(get_settings().auth, "rate_limit_login_per_min", 100)
    await get_redis().flushdb()

    for index in range(3):
        await client.post(
            "/auth/login", json={"email": f"nobody{index}@example.com", "password": "Whatever1!"}
        )
    resp = await client.post(
        "/auth/login", json={"email": "someone-else@example.com", "password": "Whatever1!"}
    )
    assert resp.status_code == 429
