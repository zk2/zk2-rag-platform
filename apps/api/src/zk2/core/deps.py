"""Common FastAPI dependencies."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

import jwt
from fastapi import Depends, Header, Request
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from zk2.auth.models import User
from zk2.core.db import get_db
from zk2.core.errors import UnauthorizedError
from zk2.core.redis_client import get_redis as _get_redis
from zk2.core.security import decode_access_token


async def get_db_dep() -> AsyncIterator[AsyncSession]:
    async for session in get_db():
        yield session


def get_redis_dep() -> Redis:
    return _get_redis()


def get_client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    if request.client:
        return request.client.host
    return None


def get_user_agent(request: Request) -> str | None:
    return request.headers.get("user-agent")


async def current_user_dep(
    db: Annotated[AsyncSession, Depends(get_db_dep)],
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise UnauthorizedError("Missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = decode_access_token(token)
    except jwt.PyJWTError as exc:
        raise UnauthorizedError("Invalid or expired token") from exc

    try:
        user_id = int(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise UnauthorizedError("Invalid token subject") from exc

    user = await db.scalar(select(User).where(User.id == user_id))
    if user is None or not user.is_active:
        raise UnauthorizedError("User not found or inactive")
    return user
