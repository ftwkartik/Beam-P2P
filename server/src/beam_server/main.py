"""Application factory.

`create_app()` wires settings, logging, middleware, exception handlers and routers into
one FastAPI instance. Kept separate from a module-level `app = FastAPI()` so tests can
build fresh, independently configured instances (see server/tests/conftest.py).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import redis.asyncio as redis
import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from beam_server.api.health import router as health_router
from beam_server.api.ice import router as ice_router
from beam_server.api.metrics import router as metrics_router
from beam_server.api.rooms import router as rooms_router
from beam_server.config import Settings, get_settings
from beam_server.errors import register_exception_handlers
from beam_server.logging import configure_logging
from beam_server.observability.middleware import RequestContextMiddleware
from beam_server.services.signaling import SignalingHub
from beam_server.store.pubsub import RoomPubSub
from beam_server.store.redis import close_redis_client, create_redis_client
from beam_server.ws.endpoint import router as ws_router

logger = structlog.get_logger(__name__)

RedisClientFactory = Callable[[str], "redis.Redis"]


def create_app(
    settings: Settings | None = None,
    *,
    redis_client_factory: RedisClientFactory = create_redis_client,
) -> FastAPI:
    """Build and return a configured FastAPI application.

    `redis_client_factory` builds the one app-lifetime Redis client used for pub/sub
    (see the lifespan below); it defaults to a real `redis.from_url(...)` client. Tests
    that want the whole app -- including its pub/sub relay -- backed by a shared
    fakeredis `FakeServer` pass their own factory here instead of only overriding the
    per-request `get_redis` dependency (see server/tests/integration/test_signaling_ws.py).
    """
    settings = settings or get_settings()
    configure_logging(level=settings.log_level, json_format=settings.log_format == "json")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.redis = redis_client_factory(settings.redis_url)
        app.state.pubsub = RoomPubSub(
            app.state.redis, app.state.signaling_hub, app.state.instance_id
        )
        app.state.pubsub.start()
        logger.info("app_startup", app_name=settings.app_name, env=settings.env)
        try:
            yield
        finally:
            await app.state.pubsub.stop()
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
    # Unprefixed, matching Prometheus convention: scrapers expect /metrics directly.
    app.include_router(metrics_router)

    return app


app = create_app()
