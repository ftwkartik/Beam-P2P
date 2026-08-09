"""Integration-test fixtures that need a real Redis instance.

These tests are skipped (not failed) when Redis is unreachable, so `pytest` still runs
cleanly on a machine that hasn't started `docker compose up -d redis`. CI always
provides a `redis` service container (see .github/workflows/ci.yml).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
import redis.asyncio as redis

INTEGRATION_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")


@pytest.fixture
async def real_redis() -> AsyncIterator[redis.Redis]:
    client = redis.from_url(INTEGRATION_REDIS_URL, decode_responses=True)
    try:
        await client.ping()
    except redis.RedisError:
        await client.aclose()
        pytest.skip(
            f"Redis not reachable at {INTEGRATION_REDIS_URL}; "
            "run `docker compose up -d redis` to enable integration tests."
        )
    else:
        await client.flushdb()
        yield client
        await client.flushdb()
        await client.aclose()
