"""Shared pytest fixtures.

Spins up real Postgres+Redis via testcontainers; runs Alembic on a fresh DB per session.
"""

from __future__ import annotations

import os
import secrets
import subprocess
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

API_DIR = Path(__file__).parent.parent


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
    os.environ["SUPER_ADMIN_EMAIL"] = "admin@test.local"
    os.environ["SUPER_ADMIN_PASSWORD"] = "AdminPass1234!"

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
            "sessions, recovery_codes, totp_secrets, oauth_accounts, users, organizations "
            "RESTART IDENTITY CASCADE"
        )


@pytest_asyncio.fixture
async def super_admin(db: AsyncSession) -> dict[str, Any]:
    """Create a super-admin user; returns dict with id/email/password."""
    from datetime import UTC, datetime

    from zk2.auth.models import User
    from zk2.core.security import hash_password

    pwd = "AdminPass1234!"
    user = User(
        email="admin@test.local",
        password_hash=hash_password(pwd),
        is_super_admin=True,
        is_active=True,
        email_verified_at=datetime.now(UTC),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return {"id": user.id, "email": user.email, "password": pwd}
