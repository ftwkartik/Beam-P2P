"""GET /rooms/{room_id}/ice-servers (docs/protocol.md §2; docs/adr/004-turn-coturn-
ephemeral-credentials.md). Uses fakeredis: `authorize()` is a pure JWT decode with no
room lookup, and the per-room rate limiter is a plain sorted set (no Lua script), so
neither path needs the real-Redis fixture other integration tests rely on.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from collections.abc import AsyncIterator

import pytest
import redis.asyncio as redis
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from beam_server.config import Settings, get_settings
from beam_server.main import create_app
from beam_server.security.tokens import Role, create_room_token
from beam_server.store.redis import get_redis

JWT_SECRET = "test-jwt-secret-at-least-32-bytes-long"  # pragma: allowlist secret
CODE_PEPPER = "test-code-pepper-at-least-32-bytes-long"  # pragma: allowlist secret
TURN_SECRET = "test-turn-secret-at-least-32-bytes-lo"  # pragma: allowlist secret


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "env": "test",
        "jwt_secret": JWT_SECRET,
        "code_pepper": CODE_PEPPER,
        "turn_secret": TURN_SECRET,
        "turn_urls": ["turn:localhost:3478?transport=udp", "turn:localhost:3478?transport=tcp"],
        "rate_limit_ice_servers_per_room_per_minute": 3,
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


def _token(*, room_id: str = "room-1", peer_id: str = "peer-1", role: Role = "creator") -> str:
    return create_room_token(
        secret=JWT_SECRET, peer_id=peer_id, room_id=room_id, role=role, ttl_seconds=3600
    )


async def _build_app(fake_redis: redis.Redis, settings: Settings) -> FastAPI:
    app = create_app(settings=settings, redis_client_factory=lambda _url: fake_redis)
    app.dependency_overrides[get_settings] = lambda: settings

    async def _override_get_redis() -> AsyncIterator[redis.Redis]:
        yield fake_redis

    app.dependency_overrides[get_redis] = _override_get_redis
    return app


@pytest.fixture
async def ice_app(fake_redis: redis.Redis) -> AsyncIterator[FastAPI]:
    app = await _build_app(fake_redis, _settings())
    async with app.router.lifespan_context(app):
        yield app


@pytest.fixture
async def client(ice_app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=ice_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_returns_stun_and_turn_with_credentials(client: AsyncClient) -> None:
    resp = await client.get(
        "/api/v1/rooms/room-1/ice-servers", headers={"Authorization": f"Bearer {_token()}"}
    )
    assert resp.status_code == 200
    servers = resp.json()["ice_servers"]

    stun = [s for s in servers if s["urls"].startswith("stun:")]
    turn = [s for s in servers if s["urls"].startswith("turn:")]
    assert stun and all(s["username"] is None for s in stun)
    assert {s["urls"] for s in turn} == {
        "turn:localhost:3478?transport=udp",
        "turn:localhost:3478?transport=tcp",
    }
    assert len({s["username"] for s in turn}) == 1  # one credential, reused across urls
    assert all(s["credential"] for s in turn)


async def test_turn_credential_is_a_valid_hmac_sha1(client: AsyncClient) -> None:
    resp = await client.get(
        "/api/v1/rooms/room-1/ice-servers",
        headers={"Authorization": f"Bearer {_token(peer_id='peer-xyz')}"},
    )
    turn_entry = next(s for s in resp.json()["ice_servers"] if s["urls"].startswith("turn:"))

    username = turn_entry["username"]
    assert username.endswith(":peer-xyz")
    expected = base64.b64encode(
        hmac.new(TURN_SECRET.encode(), username.encode(), hashlib.sha1).digest()
    ).decode()
    assert turn_entry["credential"] == expected


async def test_requires_a_bearer_token(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/rooms/room-1/ice-servers")
    assert resp.status_code == 401


async def test_rejects_a_token_scoped_to_a_different_room(client: AsyncClient) -> None:
    resp = await client.get(
        "/api/v1/rooms/room-1/ice-servers",
        headers={"Authorization": f"Bearer {_token(room_id='other-room')}"},
    )
    assert resp.status_code == 401


async def test_no_turn_urls_configured_means_stun_only(fake_redis: redis.Redis) -> None:
    app = await _build_app(fake_redis, _settings(turn_urls=[]))
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(
                "/api/v1/rooms/room-1/ice-servers", headers={"Authorization": f"Bearer {_token()}"}
            )

    servers = resp.json()["ice_servers"]
    assert servers
    assert all(s["urls"].startswith("stun:") and s["username"] is None for s in servers)


async def test_requests_are_rate_limited_per_room(client: AsyncClient) -> None:
    headers = {"Authorization": f"Bearer {_token()}"}
    for _ in range(3):
        resp = await client.get("/api/v1/rooms/room-1/ice-servers", headers=headers)
        assert resp.status_code == 200

    resp = await client.get("/api/v1/rooms/room-1/ice-servers", headers=headers)
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers


async def test_rate_limit_is_scoped_per_room_not_global(client: AsyncClient) -> None:
    headers_1 = {"Authorization": f"Bearer {_token()}"}
    headers_2 = {"Authorization": f"Bearer {_token(room_id='room-2', peer_id='peer-2')}"}

    for _ in range(3):
        assert (
            await client.get("/api/v1/rooms/room-1/ice-servers", headers=headers_1)
        ).status_code == 200

    resp = await client.get("/api/v1/rooms/room-2/ice-servers", headers=headers_2)
    assert resp.status_code == 200
