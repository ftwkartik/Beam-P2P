"""Redis client lifecycle.

Redis is the only shared state store in the MVP (see docs/adr/002-redis-state-and-pubsub.md).
One client is created at app startup and closed at shutdown; handlers reach it through
`get_redis`, a FastAPI dependency, so tests can override it with a fake.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import cast

import redis.asyncio as redis
from fastapi import Request


def create_redis_client(redis_url: str) -> redis.Redis:
    """Build a Redis client. Connections are established lazily on first use."""
    return redis.from_url(redis_url, decode_responses=True)


async def close_redis_client(client: redis.Redis) -> None:
    await client.aclose()


async def get_redis(request: Request) -> AsyncIterator[redis.Redis]:
    """FastAPI dependency yielding the app-wide Redis client."""
    yield cast(redis.Redis, request.app.state.redis)
