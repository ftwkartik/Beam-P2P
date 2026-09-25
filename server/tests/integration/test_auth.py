"""Accounts + transfer-history integration tests against a real Postgres
(Milestone 11) -- register/login/refresh/logout, and the cross-user isolation matrix
docs/testing-strategy.md calls for (docs/security.md: another user's data is 404, not
a leak of existence).

Uses fakeredis for rate limiting (unit-tested separately in test_rate_limit.py) so
these tests only need a real Postgres, not a real Redis too -- matching how the
existing room tests keep each integration test scoped to the one real dependency it's
actually exercising.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import fakeredis.aioredis
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from beam_server.config import Settings, get_settings
from beam_server.db.engine import get_session
from beam_server.main import create_app
from beam_server.store.redis import get_redis

TEST_PASSWORD = "correcthorse"  # pragma: allowlist secret -- test-only, not real


@pytest.fixture
def test_settings() -> Settings:
    return Settings(
        env="test",
        jwt_secret="a" * 32,
        database_url="postgresql+asyncpg://unused",  # only read to gate route registration
    )


@pytest.fixture
async def client(
    real_postgres: AsyncSession, test_settings: Settings
) -> AsyncIterator[AsyncClient]:
    fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    app: FastAPI = create_app(settings=test_settings, redis_client_factory=lambda _url: fake_redis)

    async def _override_get_session() -> AsyncIterator[AsyncSession]:
        yield real_postgres

    async def _override_get_redis() -> AsyncIterator[fakeredis.aioredis.FakeRedis]:
        yield fake_redis

    app.dependency_overrides[get_session] = _override_get_session
    app.dependency_overrides[get_redis] = _override_get_redis
    app.dependency_overrides[get_settings] = lambda: test_settings

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _register_and_login(client: AsyncClient, email: str) -> dict[str, str]:
    await client.post("/api/v1/auth/register", json={"email": email, "password": TEST_PASSWORD})
    response = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": TEST_PASSWORD}
    )
    body: dict[str, str] = response.json()
    return body


async def test_register_then_login_round_trip(client: AsyncClient) -> None:
    register = await client.post(
        "/api/v1/auth/register", json={"email": "a@example.com", "password": TEST_PASSWORD}
    )
    assert register.status_code == 201

    login = await client.post(
        "/api/v1/auth/login", json={"email": "a@example.com", "password": TEST_PASSWORD}
    )
    assert login.status_code == 200
    tokens = login.json()
    assert tokens["access_token"]
    assert tokens["refresh_token"]


async def test_duplicate_email_is_rejected(client: AsyncClient) -> None:
    body = {"email": "a@example.com", "password": TEST_PASSWORD}
    await client.post("/api/v1/auth/register", json=body)
    second = await client.post("/api/v1/auth/register", json=body)
    assert second.status_code == 409


async def test_wrong_password_is_rejected(client: AsyncClient) -> None:
    await client.post(
        "/api/v1/auth/register", json={"email": "a@example.com", "password": TEST_PASSWORD}
    )
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "a@example.com", "password": "wrong"},  # pragma: allowlist secret
    )
    assert response.status_code == 401


async def test_me_requires_a_valid_bearer_token(client: AsyncClient) -> None:
    no_header = await client.get("/api/v1/auth/me")
    assert no_header.status_code == 401

    garbage = await client.get("/api/v1/auth/me", headers={"Authorization": "Bearer garbage"})
    assert garbage.status_code == 401

    tokens = await _register_and_login(client, "a@example.com")
    ok = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    assert ok.status_code == 200
    assert ok.json()["email"] == "a@example.com"


async def test_refresh_rotates_and_rejects_reuse(client: AsyncClient) -> None:
    tokens = await _register_and_login(client, "a@example.com")

    refreshed = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert refreshed.status_code == 200
    assert refreshed.json()["refresh_token"] != tokens["refresh_token"]

    reused = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert reused.status_code == 401


async def test_logout_revokes_the_refresh_token(client: AsyncClient) -> None:
    tokens = await _register_and_login(client, "a@example.com")

    logout = await client.post(
        "/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]}
    )
    assert logout.status_code == 204

    refresh_after_logout = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert refresh_after_logout.status_code == 401


async def test_record_and_list_a_transfer(client: AsyncClient) -> None:
    tokens = await _register_and_login(client, "a@example.com")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    record = await client.post(
        "/api/v1/transfers",
        headers=headers,
        json={
            "direction": "sent",
            "peer_label": "some-peer",
            "outcome": "completed",
            "files": [{"name": "photo.bin", "size": 2_000_000, "sha256": "a" * 64}],
        },
    )
    assert record.status_code == 201
    transfer_id = record.json()["id"]

    listed = await client.get("/api/v1/transfers", headers=headers)
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert listed.json()[0]["id"] == transfer_id

    fetched = await client.get(f"/api/v1/transfers/{transfer_id}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["files"][0]["name"] == "photo.bin"


class TestCrossUserIsolation:
    """docs/security.md: another user's data is unreachable, not merely hidden."""

    async def test_cannot_get_another_users_transfer(self, client: AsyncClient) -> None:
        tokens_a = await _register_and_login(client, "a@example.com")
        record = await client.post(
            "/api/v1/transfers",
            headers={"Authorization": f"Bearer {tokens_a['access_token']}"},
            json={
                "direction": "sent",
                "peer_label": "peer",
                "outcome": "completed",
                "files": [{"name": "f.bin", "size": 1, "sha256": "a" * 64}],
            },
        )
        transfer_id = record.json()["id"]

        tokens_b = await _register_and_login(client, "b@example.com")
        response = await client.get(
            f"/api/v1/transfers/{transfer_id}",
            headers={"Authorization": f"Bearer {tokens_b['access_token']}"},
        )
        assert response.status_code == 404

    async def test_list_never_includes_another_users_transfers(self, client: AsyncClient) -> None:
        tokens_a = await _register_and_login(client, "a@example.com")
        await client.post(
            "/api/v1/transfers",
            headers={"Authorization": f"Bearer {tokens_a['access_token']}"},
            json={
                "direction": "sent",
                "peer_label": "peer",
                "outcome": "completed",
                "files": [{"name": "f.bin", "size": 1, "sha256": "a" * 64}],
            },
        )

        tokens_b = await _register_and_login(client, "b@example.com")
        response = await client.get(
            "/api/v1/transfers", headers={"Authorization": f"Bearer {tokens_b['access_token']}"}
        )
        assert response.status_code == 200
        assert response.json() == []

    async def test_cannot_refresh_with_another_users_token_shape(self, client: AsyncClient) -> None:
        """Not a real attack (a refresh token isn't guessable), but confirms refresh
        doesn't trust anything about *whose* token it's handed beyond the token
        itself -- a malformed/unknown token is just rejected, user-agnostic."""
        response = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": "not-a-real-token"}
        )
        assert response.status_code == 401
