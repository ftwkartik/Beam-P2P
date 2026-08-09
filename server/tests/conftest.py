"""Shared pytest fixtures for the server test suite.

Sets ENV=test before beam_server is imported, which is what lets the module-level
`app = create_app()` in beam_server.main succeed without real secrets configured (see
Settings._validate_secrets_outside_test in beam_server.config): the test environment
opts out of the "no weak secrets in production" check by declaring itself as tests.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENV", "test")

from collections.abc import AsyncIterator, Iterator

import fakeredis.aioredis
import pytest
import redis.asyncio as redis
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from beam_server.config import Settings, get_settings
from beam_server.main import create_app
from beam_server.store.redis import get_redis


def make_test_settings(**overrides: object) -> Settings:
    """Build Settings for tests: ENV=test skips the strong-secret requirement."""
    defaults: dict[str, object] = {
        "env": "test",
        "log_format": "console",
        "redis_url": "redis://localhost:6379/15",
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


@pytest.fixture
def fake_redis() -> Iterator[redis.Redis]:
    """A fakeredis instance so unit tests never touch a real Redis server."""
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client


@pytest.fixture
def test_settings() -> Settings:
    return make_test_settings()


@pytest.fixture
def app(fake_redis: redis.Redis, test_settings: Settings) -> FastAPI:
    """A FastAPI app with Redis and Settings overridden for isolated testing."""
    application = create_app(settings=test_settings)

    async def _override_get_redis() -> AsyncIterator[redis.Redis]:
        yield fake_redis

    application.dependency_overrides[get_redis] = _override_get_redis
    application.dependency_overrides[get_settings] = lambda: test_settings
    return application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """An httpx client bound to the app via ASGI transport."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
