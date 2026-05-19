"""FastAPI application entry point."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from zk2 import __version__
from zk2.admin.router import router as admin_router
from zk2.auth.router import public_router as access_router
from zk2.auth.router import router as auth_router
from zk2.bots.router import router as bots_router
from zk2.chat.ws import router as chat_ws_router
from zk2.config import get_settings
from zk2.core.arq import close_arq
from zk2.core.db import dispose_engine, get_engine
from zk2.core.errors import register_exception_handlers
from zk2.core.logging import configure_logging
from zk2.core.redis_client import close_redis
from zk2.core.telemetry import instrument_sqlalchemy_engine, setup_telemetry
from zk2.health import router as health_router
from zk2.llm.router import router as providers_router
from zk2.sources.router import router as sources_router


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    engine = get_engine()
    instrument_sqlalchemy_engine(engine.sync_engine)
    try:
        yield
    finally:
        await dispose_engine()
        await close_redis()
        await close_arq()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging()

    app = FastAPI(
        title=settings.app.name,
        version=__version__,
        lifespan=_lifespan,
        root_path="/api" if settings.is_prod else "",
    )

    # CORS — strict allowlist; do NOT use "*" with allow_credentials in prod
    allowed_origins = [settings.app.base_url] if settings.is_prod else ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=not settings.is_prod,  # credentials only on permissive dev
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)
    setup_telemetry(app)

    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(access_router)
    app.include_router(admin_router)
    app.include_router(providers_router)
    app.include_router(sources_router)
    app.include_router(bots_router)
    app.include_router(chat_ws_router)

    return app


app = create_app()
