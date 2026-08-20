"""Room lifecycle integration tests (docs/testing-strategy.md, "Integration test
inventory"). These need a real Redis: the room-join and burn logic uses Lua scripts
for atomicity, and fakeredis doesn't implement EVAL/EVALSHA.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator

import pytest
import redis.asyncio as redis
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from beam_server.config import Settings, get_settings
from beam_server.main import create_app
from beam_server.store.redis import get_redis

# Mirrors tests/integration/conftest.py's default. Not imported from there directly:
# cross-importing between test modules by dotted path is fragile under pytest's
# import modes when there's no package `__init__.py` (as here, deliberately).
_INTEGRATION_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "env": "test",
        "redis_url": _INTEGRATION_REDIS_URL,
        "jwt_secret": "test-jwt-secret-at-least-32-bytes-long",  # pragma: allowlist secret
        "code_pepper": "test-code-pepper-at-least-32-bytes-long",  # pragma: allowlist secret
        # Fast TTLs so tests don't have to wait around, and a low join limit so the
        # burn tests don't need many requests.
        "room_waiting_ttl_seconds": 60,
        "room_max_join_attempts": 5,
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


@pytest.fixture
async def rooms_app(real_redis: redis.Redis) -> AsyncIterator[FastAPI]:
    settings = _settings()
    app = create_app(settings=settings)
    app.dependency_overrides[get_settings] = lambda: settings

    async def _override_get_redis() -> AsyncIterator[redis.Redis]:
        yield real_redis

    app.dependency_overrides[get_redis] = _override_get_redis

    async with app.router.lifespan_context(app):
        yield app


@pytest.fixture
async def client(rooms_app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=rooms_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _create_room(client: AsyncClient) -> dict:
    response = await client.post("/api/v1/rooms")
    assert response.status_code == 201, response.text
    return response.json()


class TestCreateRoom:
    async def test_create_room_returns_a_well_formed_code(self, client: AsyncClient) -> None:
        room = await _create_room(client)
        assert room["room_id"]
        assert room["token"]
        parts = room["code"].split("-")
        assert len(parts) == 4
        assert parts[0] == str(room["nameplate"])

    async def test_create_room_persists_all_keys_with_a_ttl(
        self, client: AsyncClient, real_redis: redis.Redis
    ) -> None:
        room = await _create_room(client)
        room_key = f"room:{room['room_id']}"
        nameplate_key = f"nameplate:{room['nameplate']}"

        assert await real_redis.exists(room_key)
        assert await real_redis.exists(nameplate_key)
        # docs/data-model.md: "There are no unbounded keys." Every key must have a TTL.
        assert await real_redis.ttl(room_key) > 0
        assert await real_redis.ttl(nameplate_key) > 0

    async def test_two_rooms_get_different_nameplates_and_codes(self, client: AsyncClient) -> None:
        a = await _create_room(client)
        b = await _create_room(client)
        assert a["nameplate"] != b["nameplate"]
        assert a["code"] != b["code"]


class TestJoinRoom:
    async def test_join_with_correct_code_succeeds(self, client: AsyncClient) -> None:
        room = await _create_room(client)
        response = await client.post("/api/v1/rooms/join", json={"code": room["code"]})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["room_id"] == room["room_id"]
        assert body["token"]
        assert body["token"] != room["token"]

    async def test_join_with_wrong_code_returns_invalid_code(self, client: AsyncClient) -> None:
        room = await _create_room(client)
        # "falcon-lantern-tiger" is a well-formed code (real wordlist words) that is not
        # this room's actual (randomly generated) code.
        bad_code = f"{room['nameplate']}-falcon-lantern-tiger"
        response = await client.post("/api/v1/rooms/join", json={"code": bad_code})
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "INVALID_CODE"

    async def test_unknown_nameplate_and_wrong_code_are_indistinguishable(
        self, client: AsyncClient
    ) -> None:
        """docs/security.md §1: an attacker must not learn whether a nameplate exists."""
        room = await _create_room(client)
        wrong_words = f"{room['nameplate']}-falcon-lantern-tiger"
        unknown = "9999-falcon-lantern-tiger"

        wrong_response = await client.post("/api/v1/rooms/join", json={"code": wrong_words})
        unknown_response = await client.post("/api/v1/rooms/join", json={"code": unknown})

        assert wrong_response.status_code == unknown_response.status_code == 404
        assert (
            wrong_response.json()["error"]["code"]
            == unknown_response.json()["error"]["code"]
            == "INVALID_CODE"
        )

    async def test_malformed_code_returns_invalid_code(self, client: AsyncClient) -> None:
        response = await client.post("/api/v1/rooms/join", json={"code": "not a real code"})
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "INVALID_CODE"

    async def test_second_correct_join_returns_room_full(self, client: AsyncClient) -> None:
        room = await _create_room(client)
        first = await client.post("/api/v1/rooms/join", json={"code": room["code"]})
        assert first.status_code == 200

        second = await client.post("/api/v1/rooms/join", json={"code": room["code"]})
        assert second.status_code == 409
        assert second.json()["error"]["code"] == "ROOM_FULL"

    async def test_five_wrong_attempts_burns_the_room(self, client: AsyncClient) -> None:
        room = await _create_room(client)
        # Well-formed (real wordlist words) but wrong, so each attempt reaches the
        # HMAC comparison and increments the burn counter -- unlike a malformed code,
        # which is rejected before ever touching room state.
        bad_code = f"{room['nameplate']}-falcon-lantern-tiger"

        for _ in range(5):
            response = await client.post("/api/v1/rooms/join", json={"code": bad_code})
            assert response.status_code == 404

        # The 6th attempt -- even with the *correct* code -- must see the room as burned.
        burned_response = await client.post("/api/v1/rooms/join", json={"code": room["code"]})
        assert burned_response.status_code == 410
        assert burned_response.json()["error"]["code"] == "ROOM_BURNED"

    async def test_concurrent_correct_joins_only_one_succeeds(self, client: AsyncClient) -> None:
        room = await _create_room(client)

        results = await asyncio.gather(
            *[client.post("/api/v1/rooms/join", json={"code": room["code"]}) for _ in range(5)]
        )
        statuses = sorted(r.status_code for r in results)
        assert statuses.count(200) == 1
        assert statuses.count(409) == 4


class TestIceServers:
    async def test_ice_servers_requires_a_token(self, client: AsyncClient) -> None:
        room = await _create_room(client)
        response = await client.get(f"/api/v1/rooms/{room['room_id']}/ice-servers")
        assert response.status_code == 401

    async def test_ice_servers_with_valid_token_returns_stun(self, client: AsyncClient) -> None:
        room = await _create_room(client)
        response = await client.get(
            f"/api/v1/rooms/{room['room_id']}/ice-servers",
            headers={"Authorization": f"Bearer {room['token']}"},
        )
        assert response.status_code == 200
        servers = response.json()["ice_servers"]
        assert any("stun:" in s["urls"] for s in servers)

    async def test_ice_servers_rejects_a_token_for_a_different_room(
        self, client: AsyncClient
    ) -> None:
        room_a = await _create_room(client)
        room_b = await _create_room(client)
        response = await client.get(
            f"/api/v1/rooms/{room_b['room_id']}/ice-servers",
            headers={"Authorization": f"Bearer {room_a['token']}"},
        )
        assert response.status_code == 401

    async def test_ice_servers_rejects_a_garbage_token(self, client: AsyncClient) -> None:
        room = await _create_room(client)
        response = await client.get(
            f"/api/v1/rooms/{room['room_id']}/ice-servers",
            headers={"Authorization": "Bearer not-a-real-token"},
        )
        assert response.status_code == 401


class TestCloseRoom:
    async def test_creator_can_close_the_room(
        self, client: AsyncClient, real_redis: redis.Redis
    ) -> None:
        room = await _create_room(client)
        response = await client.delete(
            f"/api/v1/rooms/{room['room_id']}",
            headers={"Authorization": f"Bearer {room['token']}"},
        )
        assert response.status_code == 204
        assert not await real_redis.exists(f"room:{room['room_id']}")
        assert not await real_redis.exists(f"nameplate:{room['nameplate']}")

    async def test_joiner_cannot_close_the_room(self, client: AsyncClient) -> None:
        room = await _create_room(client)
        joined = (await client.post("/api/v1/rooms/join", json={"code": room["code"]})).json()

        response = await client.delete(
            f"/api/v1/rooms/{room['room_id']}",
            headers={"Authorization": f"Bearer {joined['token']}"},
        )
        assert response.status_code == 401

    async def test_close_without_a_token_is_unauthorized(self, client: AsyncClient) -> None:
        room = await _create_room(client)
        response = await client.delete(f"/api/v1/rooms/{room['room_id']}")
        assert response.status_code == 401

    async def test_closed_room_code_no_longer_works(self, client: AsyncClient) -> None:
        room = await _create_room(client)
        await client.delete(
            f"/api/v1/rooms/{room['room_id']}",
            headers={"Authorization": f"Bearer {room['token']}"},
        )
        response = await client.post("/api/v1/rooms/join", json={"code": room["code"]})
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "INVALID_CODE"


class TestRateLimiting:
    async def test_join_rate_limit_returns_429_with_retry_after(
        self, client: AsyncClient, rooms_app: FastAPI
    ) -> None:
        settings = rooms_app.dependency_overrides[get_settings]()
        limit = settings.rate_limit_join_per_minute

        # The per-IP join limit is checked before the code is even parsed, so a
        # malformed code is fine for driving up the request count.
        responses = [
            await client.post("/api/v1/rooms/join", json={"code": "1-a-b-c"})
            for _ in range(limit + 1)
        ]
        assert responses[-1].status_code == 429
        assert responses[-1].json()["error"]["code"] == "RATE_LIMITED"
        assert "Retry-After" in responses[-1].headers
