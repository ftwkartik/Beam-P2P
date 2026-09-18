"""ApiClient against a real beam_server app (see docs/adr/007's "injectable
factories" pattern, applied here to the CLI's own REST client). `join_room` needs a
real Redis (Lua-scripted atomic join; fakeredis can't run it -- see conftest.py's
`real_redis`), so it gets its own app fixture instead of the fakeredis-backed one the
rest of these use.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import fakeredis.aioredis
import httpx
import pytest
import redis.asyncio as redis
from fastapi import FastAPI

from beam_cli.api import ApiClient, ApiError
from beam_server.config import Settings, get_settings
from beam_server.main import create_app
from beam_server.store.redis import get_redis

_JWT_SECRET = "test-jwt-secret-at-least-32-bytes-long"  # pragma: allowlist secret
_CODE_PEPPER = "test-code-pepper-at-least-32-bytes-long"  # pragma: allowlist secret


def _client_for(app: FastAPI) -> ApiClient:
    return ApiClient("http://testserver", transport=httpx.ASGITransport(app=app))


@pytest.fixture
def api_client() -> ApiClient:
    """Fakeredis-backed: fine for anything that doesn't need `join_room`'s Lua script."""
    fake_server = fakeredis.FakeServer()
    settings = Settings(
        env="test",
        jwt_secret=_JWT_SECRET,
        code_pepper=_CODE_PEPPER,
        allowed_origins=["http://allowed.example"],
    )
    app = create_app(
        settings=settings,
        redis_client_factory=lambda _url: fakeredis.aioredis.FakeRedis(
            server=fake_server, decode_responses=True
        ),
    )
    app.dependency_overrides[get_settings] = lambda: settings

    async def _override_get_redis() -> AsyncIterator[redis.Redis]:
        client = fakeredis.aioredis.FakeRedis(server=fake_server, decode_responses=True)
        try:
            yield client
        finally:
            await client.aclose()

    app.dependency_overrides[get_redis] = _override_get_redis
    return _client_for(app)


@pytest.fixture
def real_redis_api_client(real_redis: redis.Redis) -> ApiClient:
    settings = Settings(
        env="test",
        jwt_secret=_JWT_SECRET,
        code_pepper=_CODE_PEPPER,
        allowed_origins=["http://allowed.example"],
    )
    # redis_url is unused: real_redis (an already-connected fixture) is injected
    # directly below, for both the app-lifetime client and the per-request one.
    app = create_app(settings=settings, redis_client_factory=lambda _url: real_redis)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_redis] = lambda: real_redis
    return _client_for(app)


async def test_create_room_returns_a_usable_token(api_client: ApiClient) -> None:
    created = await api_client.create_room()
    assert created.room_id
    assert created.token
    assert created.nameplate >= 1

    ice_servers = await api_client.get_ice_servers(created.room_id, created.token)
    assert len(ice_servers) >= 1


async def test_join_room_with_the_created_code(real_redis_api_client: ApiClient) -> None:
    created = await real_redis_api_client.create_room()
    joined = await real_redis_api_client.join_room(created.code)
    assert joined.room_id == created.room_id
    assert joined.token != created.token


async def test_join_room_with_a_wrong_code_raises_api_error(
    real_redis_api_client: ApiClient,
) -> None:
    with pytest.raises(ApiError) as exc_info:
        await real_redis_api_client.join_room("1-wrong-words-here")
    assert exc_info.value.status == 404


async def test_close_room_requires_a_valid_token(api_client: ApiClient) -> None:
    created = await api_client.create_room()
    with pytest.raises(ApiError) as exc_info:
        await api_client.close_room(created.room_id, "not-a-real-token")
    assert exc_info.value.status == 401


async def test_signaling_ws_url_derives_from_the_base_url() -> None:
    assert ApiClient("http://localhost:8080").signaling_ws_url() == "ws://localhost:8080/ws"
    assert ApiClient("https://beam.example").signaling_ws_url() == "wss://beam.example/ws"
