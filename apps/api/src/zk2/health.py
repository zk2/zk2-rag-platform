"""Healthcheck endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from zk2.core.deps import get_db_dep, get_redis_dep

router = APIRouter(tags=["health"])


@router.get("/health", summary="Liveness")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/db", summary="DB connectivity")
async def health_db(
    db: Annotated[AsyncSession, Depends(get_db_dep)],
) -> JSONResponse:
    try:
        await db.execute(text("select 1"))
    except Exception as exc:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "degraded", "error": str(exc)},
        )
    return JSONResponse({"status": "ok"})


@router.get("/health/redis", summary="Redis connectivity")
async def health_redis(
    redis: Annotated[Redis, Depends(get_redis_dep)],
) -> JSONResponse:
    try:
        pong = await redis.ping()
    except Exception as exc:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "degraded", "error": str(exc)},
        )
    return JSONResponse({"status": "ok" if pong else "degraded"})
