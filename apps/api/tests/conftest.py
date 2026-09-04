"""Shared pytest fixtures.

Spins up real Postgres+Redis via testcontainers; runs Alembic on a fresh DB per session.
"""

from __future__ import annotations

import os

# Must happen before anything imports zk2.config: the developer's .env carries
# real provider keys, SMTP credentials and an OTLP endpoint, and tests must not
# see any of them. Module level, not a fixture - collection imports app code.
os.environ["ZK2_DISABLE_DOTENV"] = "1"
os.environ.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)
os.environ.pop("SENTRY_DSN", None)

import secrets
import subprocess
import tempfile
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

API_DIR = Path(__file__).parent.parent
_UPLOAD_DIR = Path(tempfile.mkdtemp(prefix="zk2-test-uploads-"))


@pytest.fixture(scope="session")
def _postgres() -> Iterator[PostgresContainer]:
    with PostgresContainer("pgvector/pgvector:pg16") as pg:
        yield pg


@pytest.fixture(scope="session")
def _redis() -> Iterator[RedisContainer]:
    with RedisContainer("redis:7-alpine") as r:
        yield r


@pytest.fixture(scope="session", autouse=True)
def _env(_postgres: PostgresContainer, _redis: RedisContainer) -> Iterator[None]:
    """Configure env vars BEFORE importing app modules."""
    # Hermetic: ignore the developer's .env (real API keys, SMTP, OTLP endpoint)
    os.environ["ZK2_DISABLE_DOTENV"] = "1"
    os.environ["APP_ENV"] = "test"
    os.environ["APP_SECRET_KEY"] = secrets.token_urlsafe(48)
    os.environ["DB_HOST"] = _postgres.get_container_host_ip()
    os.environ["DB_PORT"] = str(_postgres.get_exposed_port(5432))
    os.environ["DB_NAME"] = _postgres.dbname
    os.environ["DB_USER"] = _postgres.username
    os.environ["DB_PASSWORD"] = _postgres.password
    os.environ["REDIS_URL"] = (
        f"redis://{_redis.get_container_host_ip()}:{_redis.get_exposed_port(6379)}/0"
    )
    os.environ["EMAIL_SENDER"] = "test@example.com"
    os.environ["APP_BASE_URL"] = "http://test"
    os.environ["SUPER_ADMIN_EMAIL"] = "admin@example.com"
    os.environ["SUPER_ADMIN_PASSWORD"] = "AdminPass1234!"
    os.environ["LOCAL_UPLOAD_DIR"] = str(_UPLOAD_DIR)
    # No telemetry, no outbound calls from tests
    os.environ.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)
    os.environ.pop("SENTRY_DSN", None)
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "EMAIL_PASSWORD"):
        os.environ.pop(key, None)

    # Clear any cached settings
    from zk2.config import get_settings

    get_settings.cache_clear()

    # Run Alembic migrations
    subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        cwd=API_DIR,
        check=True,
        env={**os.environ},
    )
    yield


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    from zk2.config import get_settings

    eng = create_async_engine(str(get_settings().db.dsn))
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest_asyncio.fixture
async def db(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    from zk2.main import create_app

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture(autouse=True)
async def _cleanup_db(engine: AsyncEngine) -> AsyncIterator[None]:
    """Truncate all data tables between tests; preserve schema."""
    yield
    async with engine.begin() as conn:
        await conn.exec_driver_sql(
            "TRUNCATE audit_log, memberships, invites, access_requests, magic_links, "
            "sessions, recovery_codes, totp_secrets, oauth_accounts, users, organizations, "
            "usage_events, messages, conversations, bot_source, bot_versions, bots, "
            "source_bm25, source_embeddings, source_chunks, sources, llm_providers "
            "RESTART IDENTITY CASCADE"
        )


@pytest_asyncio.fixture(autouse=True)
async def _reset_process_singletons() -> AsyncIterator[None]:
    """Dispose process-wide singletons between tests.

    Engine, redis and arq pools are bound to the event loop that created them,
    and pytest-asyncio gives every test a fresh loop - without this, asyncpg
    fails with "attached to a different loop".
    """
    yield
    from zk2.core import redis_client
    from zk2.core.arq import close_arq
    from zk2.core.db import dispose_engine
    from zk2.core.redis_client import close_redis

    # Rate-limit buckets live in Redis and would otherwise leak across tests:
    # a dozen logins as the same user from the same IP trip the login limiter.
    if redis_client._client is not None:
        await redis_client._client.flushdb()

    await dispose_engine()
    await close_redis()
    await close_arq()


@pytest_asyncio.fixture
async def super_admin(db: AsyncSession) -> dict[str, Any]:
    """Create a super-admin user; returns dict with id/email/password."""
    from datetime import UTC, datetime

    from zk2.auth.models import User
    from zk2.core.security import hash_password

    pwd = "AdminPass1234!"
    user = User(
        email="admin@example.com",
        password_hash=hash_password(pwd),
        is_super_admin=True,
        is_active=True,
        email_verified_at=datetime.now(UTC),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return {"id": user.id, "email": user.email, "password": pwd}


@pytest_asyncio.fixture
async def org_owner(db: AsyncSession) -> dict[str, Any]:
    """A user with one organization and an owner membership."""
    from datetime import UTC, datetime

    from zk2.auth.models import Membership, Organization, User
    from zk2.core.security import hash_password

    pwd = "OwnerPass1234!"
    user = User(
        email="owner@example.com",
        password_hash=hash_password(pwd),
        is_active=True,
        email_verified_at=datetime.now(UTC),
    )
    db.add(user)
    await db.flush()

    org = Organization(slug="acme", name="Acme Inc")
    db.add(org)
    await db.flush()

    db.add(Membership(user_id=user.id, org_id=org.id, role="owner"))
    await db.commit()
    return {"user_id": user.id, "email": user.email, "password": pwd, "org_id": org.id}


@pytest_asyncio.fixture
async def owner_client(client: AsyncClient, org_owner: dict[str, Any]) -> AsyncClient:
    """Client already carrying the owner's bearer token and org header."""
    resp = await client.post(
        "/auth/login", json={"email": org_owner["email"], "password": org_owner["password"]}
    )
    assert resp.status_code == 200, resp.text
    client.headers.update(
        {
            "Authorization": f"Bearer {resp.json()['access_token']}",
            "X-Org-Id": str(org_owner["org_id"]),
        }
    )
    return client
