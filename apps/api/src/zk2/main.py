"""FastAPI application entry point."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# IMPORTANT: import models BEFORE any router so all tables are registered
# on Base.metadata and ForeignKey strings can resolve.
import zk2.models_registry  # noqa: F401
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
from zk2.core.metrics_middleware import metrics_middleware
from zk2.core.redis_client import close_redis
from zk2.core.request_context import request_context_middleware
from zk2.core.telemetry import instrument_sqlalchemy_engine, setup_telemetry
from zk2.core.tracing import flush_traces
from zk2.health import router as health_router
from zk2.llm.router import models_router
from zk2.llm.router import router as providers_router
from zk2.observability.router import router as observability_router
from zk2.orgs.router import router as org_settings_router
from zk2.pipelines.router import router as pipelines_router
from zk2.sources.router import router as sources_router


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    engine = get_engine()
    instrument_sqlalchemy_engine(engine.sync_engine)
    try:
        yield
    finally:
        await flush_traces()
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

    # CORS - explicit allowlist in every environment. "*" together with
    # credentials is exactly the combination this project set out not to repeat.
    allowed_origins = [settings.app.base_url]
    if not settings.is_prod:
        allowed_origins += ["http://localhost:3000", "http://127.0.0.1:3000"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=sorted(set(allowed_origins)),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Registered last runs first: request context wraps the metrics middleware,
    # so a metric emitted during the request already has the log context bound.
    app.middleware("http")(metrics_middleware)
    app.middleware("http")(request_context_middleware)

    register_exception_handlers(app)
    setup_telemetry(app)

    app.include_router(health_router)
    app.include_router(observability_router)
    app.include_router(auth_router)
    app.include_router(access_router)
    app.include_router(admin_router)
    app.include_router(providers_router)
    app.include_router(models_router)
    app.include_router(org_settings_router)
    app.include_router(sources_router)
    app.include_router(bots_router)
    app.include_router(pipelines_router)
    app.include_router(chat_ws_router)

    return app


app = create_app()
