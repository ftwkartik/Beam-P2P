"""Liveness and readiness endpoints."""

from __future__ import annotations

import redis.asyncio as redis
from fastapi import FastAPI
from httpx import AsyncClient

from beam_server.store.redis import get_redis


async def test_health_always_ok(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_health_available_under_api_v1_prefix(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health")
    assert response.status_code == 200


async def test_ready_ok_when_redis_reachable(client: AsyncClient) -> None:
    response = await client.get("/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["app"] == "Beam"


async def test_ready_returns_503_when_redis_unreachable(app: FastAPI, client: AsyncClient) -> None:
    class _BrokenRedis:
        async def ping(self) -> bool:
            raise redis.RedisError("connection refused")

    async def _broken_get_redis() -> _BrokenRedis:
        return _BrokenRedis()

    app.dependency_overrides[get_redis] = _broken_get_redis

    response = await client.get("/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "NOT_READY"
    assert "request_id" in body["error"]
