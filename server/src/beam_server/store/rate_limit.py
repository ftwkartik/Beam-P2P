"""Redis sliding-window rate limiter (docs/security.md §6, "Rate limiting").

Each (scope, key) pair -- e.g. `("join_ip", "203.0.113.7")` -- gets its own sorted set
keyed `rl:{scope}:{key}` (docs/data-model.md's Redis keyspace), where the score of each
member is the timestamp it was recorded at. Checking the limit means dropping entries
older than the window and counting what's left.

This is a check-then-act sequence (not a single atomic Lua script), so under very high
concurrency a handful of requests right at the boundary could slip through. That's an
accepted trade-off for a rate limiter, which only needs to bound abuse, not enforce an
exact quota -- see docs/adr/002-redis-state-and-pubsub.md's discussion of what does and
doesn't need Lua-script atomicity.
"""

from __future__ import annotations

import time
import uuid

import redis.asyncio as redis


class RateLimitExceeded(Exception):
    """Raised when a caller has exceeded their limit. `retry_after` is in seconds."""

    def __init__(self, *, scope: str, key: str, retry_after: int) -> None:
        self.scope = scope
        self.key = key
        self.retry_after = retry_after
        super().__init__(f"rate limit exceeded for {scope}:{key}, retry after {retry_after}s")


def _redis_key(scope: str, key: str) -> str:
    return f"rl:{scope}:{key}"


async def check_rate_limit(
    redis_client: redis.Redis,
    *,
    scope: str,
    key: str,
    limit: int,
    window_seconds: int,
) -> None:
    """Raise `RateLimitExceeded` if `key` has made `limit` or more calls to `scope`
    within the trailing `window_seconds`. Otherwise records this call and returns.
    """
    redis_key = _redis_key(scope, key)
    now = time.time()
    window_start = now - window_seconds

    await redis_client.zremrangebyscore(redis_key, 0, window_start)
    count = await redis_client.zcard(redis_key)

    if count >= limit:
        raise RateLimitExceeded(scope=scope, key=key, retry_after=window_seconds)

    pipe = redis_client.pipeline()
    # A random member (not just the timestamp) avoids collisions when two calls in
    # the same millisecond would otherwise overwrite one member in the sorted set.
    pipe.zadd(redis_key, {f"{now}:{uuid.uuid4().hex}": now})
    pipe.expire(redis_key, window_seconds)
    await pipe.execute()
