"""Sliding-window rate limiter (docs/security.md §6). Uses fakeredis: unlike the room
Lua scripts, this only needs plain sorted-set commands, which fakeredis supports.
"""

from __future__ import annotations

import pytest
import redis.asyncio as redis

from beam_server.store.rate_limit import RateLimitExceeded, check_rate_limit

# `fake_redis` comes from server/tests/conftest.py.


async def test_allows_calls_under_the_limit(fake_redis: redis.Redis) -> None:
    for _ in range(3):
        await check_rate_limit(fake_redis, scope="test", key="a", limit=3, window_seconds=60)


async def test_rejects_the_call_that_reaches_the_limit(fake_redis: redis.Redis) -> None:
    for _ in range(3):
        await check_rate_limit(fake_redis, scope="test", key="a", limit=3, window_seconds=60)
    with pytest.raises(RateLimitExceeded):
        await check_rate_limit(fake_redis, scope="test", key="a", limit=3, window_seconds=60)


async def test_exceeded_error_carries_scope_key_and_retry_after(
    fake_redis: redis.Redis,
) -> None:
    await check_rate_limit(fake_redis, scope="test", key="a", limit=1, window_seconds=42)
    with pytest.raises(RateLimitExceeded) as exc_info:
        await check_rate_limit(fake_redis, scope="test", key="a", limit=1, window_seconds=42)
    assert exc_info.value.scope == "test"
    assert exc_info.value.key == "a"
    assert exc_info.value.retry_after == 42


async def test_different_keys_have_independent_limits(fake_redis: redis.Redis) -> None:
    await check_rate_limit(fake_redis, scope="test", key="a", limit=1, window_seconds=60)
    # "b" has made no calls yet, so it must not be affected by "a" being at its limit.
    await check_rate_limit(fake_redis, scope="test", key="b", limit=1, window_seconds=60)


async def test_different_scopes_have_independent_limits(fake_redis: redis.Redis) -> None:
    await check_rate_limit(fake_redis, scope="scope-1", key="a", limit=1, window_seconds=60)
    await check_rate_limit(fake_redis, scope="scope-2", key="a", limit=1, window_seconds=60)


async def test_old_entries_outside_the_window_are_not_counted(fake_redis: redis.Redis) -> None:
    # A 0-second window means every previous entry is immediately "old".
    for _ in range(5):
        await check_rate_limit(fake_redis, scope="test", key="a", limit=1, window_seconds=0)
