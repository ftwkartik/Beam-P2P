"""Liveness and readiness endpoints.

`/health` answers "is the process up" and never depends on anything external, so an
orchestrator can use it to decide whether to restart the container. `/ready` answers
"can this instance actually serve traffic" and checks Redis, per docs/protocol.md.
"""

from __future__ import annotations

from typing import Annotated

import redis.asyncio as redis
import structlog
from fastapi import APIRouter, Depends

from beam_server.config import Settings, get_settings
from beam_server.errors import NotReadyError
from beam_server.store.redis import get_redis

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["ops"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe: always returns 200 if the process can handle a request."""
    return {"status": "ok"}


@router.get("/ready")
async def ready(
    redis_client: Annotated[redis.Redis, Depends(get_redis)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, str]:
    """Readiness probe: verifies Redis is reachable before accepting real traffic."""
    try:
        await redis_client.ping()
    except redis.RedisError as exc:
        logger.warning("readiness_check_failed", dependency="redis", error=str(exc))
        raise NotReadyError("Redis is not reachable.", details={"dependency": "redis"}) from exc
    return {"status": "ok", "app": settings.app_name}
