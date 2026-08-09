"""End-to-end check that /ready reflects a real Redis instance, not just a fake."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import redis.asyncio as redis
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from beam_server.config import Settings, get_settings
from beam_server.main import create_app
from beam_server.store.redis import get_redis

INTEGRATION_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")


def _settings(redis_url: str) -> Settings:
    return Settings(env="test", redis_url=redis_url)  # type: ignore[call-arg]


async def test_ready_reports_ok_against_real_redis(real_redis: redis.Redis) -> None:
    """Exercises the *real* startup path: the lifespan creates app.state.redis, and
    the default `get_redis` dependency reads it back (no override, unlike the fake-redis
    unit tests). httpx's ASGITransport does not send lifespan events on its own, so the
    app's lifespan is driven explicitly via `app.router.lifespan_context`.
    """
    settings = _settings(INTEGRATION_REDIS_URL)
    app: FastAPI = create_app(settings=settings)
    app.dependency_overrides[get_settings] = lambda: settings

    transport = ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        response = await client.get("/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_ready_reports_503_when_pointed_at_an_unreachable_redis() -> None:
    settings = _settings("redis://127.0.0.1:1/0")
    app: FastAPI = create_app(settings=settings)
    app.dependency_overrides[get_settings] = lambda: settings

    async def _real_get_redis() -> AsyncIterator[redis.Redis]:
        client = redis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=1)
        try:
            yield client
        finally:
            await client.aclose()

    app.dependency_overrides[get_redis] = _real_get_redis

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/ready")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "NOT_READY"
