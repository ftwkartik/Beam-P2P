"""Application factory.

`create_app()` wires settings, logging, middleware, exception handlers and routers into
one FastAPI instance. Kept separate from a module-level `app = FastAPI()` so tests can
build fresh, independently configured instances (see server/tests/conftest.py).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from beam_server.api.health import router as health_router
from beam_server.api.ice import router as ice_router
from beam_server.api.rooms import router as rooms_router
from beam_server.config import Settings, get_settings
from beam_server.errors import register_exception_handlers
from beam_server.logging import configure_logging
from beam_server.observability.middleware import RequestContextMiddleware
from beam_server.services.signaling import SignalingHub
from beam_server.store.redis import close_redis_client, create_redis_client
from beam_server.ws.endpoint import router as ws_router

logger = structlog.get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and return a configured FastAPI application."""
    settings = settings or get_settings()
    configure_logging(level=settings.log_level, json_format=settings.log_format == "json")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.redis = create_redis_client(settings.redis_url)
        logger.info("app_startup", app_name=settings.app_name, env=settings.env)
        try:
            yield
        finally:
            await close_redis_client(app.state.redis)
            logger.info("app_shutdown")

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = settings
    # One hub and one instance ID per process, for the lifetime of the app (not
    # per-request): the hub holds live WebSocket references, which only make sense
    # scoped to this process (docs/architecture.md §5, "Scaling model").
    app.state.signaling_hub = SignalingHub()
    app.state.instance_id = uuid.uuid4().hex

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type", "Authorization"],
    )
    app.add_middleware(RequestContextMiddleware)

    register_exception_handlers(app)

    app.include_router(health_router, prefix="/api/v1")
    # Also exposed unprefixed for container/orchestrator health checks.
    app.include_router(health_router)
    app.include_router(rooms_router, prefix="/api/v1")
    app.include_router(ice_router, prefix="/api/v1")
    app.include_router(ws_router)

    return app


app = create_app()
