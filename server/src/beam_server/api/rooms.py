"""Room lifecycle endpoints (docs/protocol.md §2).

Request/response bodies are defined here rather than in a separate `schemas/`
package: Beam's REST surface is small (see docs/architecture.md's module layout), and
co-locating them keeps the wire contract next to the route that uses it.
"""

from __future__ import annotations

from typing import Annotated

import redis.asyncio as redis
import structlog
from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field

from beam_server.config import Settings, get_settings
from beam_server.errors import RateLimitedError, UnauthorizedError
from beam_server.security.client_ip import get_client_ip
from beam_server.services.rooms import RoomService
from beam_server.store.rate_limit import RateLimitExceeded, check_rate_limit
from beam_server.store.redis import get_redis
from beam_server.store.rooms_repo import RoomsRepository

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/rooms", tags=["rooms"])


def get_room_service(
    redis_client: Annotated[redis.Redis, Depends(get_redis)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> RoomService:
    return RoomService(
        repo=RoomsRepository(redis_client),
        redis_client=redis_client,
        jwt_secret=settings.jwt_secret.get_secret_value(),
        code_pepper=settings.code_pepper.get_secret_value(),
        waiting_ttl_seconds=settings.room_waiting_ttl_seconds,
        max_join_attempts=settings.room_max_join_attempts,
        token_ttl_seconds=settings.room_token_ttl_seconds,
        join_per_nameplate_limit=settings.rate_limit_join_per_nameplate_per_minute,
    )


def extract_bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise UnauthorizedError("A bearer room token is required.")
    return authorization.split(" ", 1)[1].strip()


async def _enforce_rate_limit(
    redis_client: redis.Redis, *, scope: str, key: str, limit: int, window_seconds: int
) -> None:
    try:
        await check_rate_limit(
            redis_client, scope=scope, key=key, limit=limit, window_seconds=window_seconds
        )
    except RateLimitExceeded as exc:
        raise RateLimitedError(
            "Too many requests. Please slow down.", retry_after=exc.retry_after
        ) from exc


class CreateRoomResponse(BaseModel):
    room_id: str
    code: str
    nameplate: int
    expires_at: float
    token: str


class JoinRoomRequest(BaseModel):
    code: str = Field(max_length=64)


class JoinRoomResponse(BaseModel):
    room_id: str
    expires_at: float
    token: str


@router.post("", status_code=201, response_model=CreateRoomResponse)
async def create_room(
    request: Request,
    room_service: Annotated[RoomService, Depends(get_room_service)],
    redis_client: Annotated[redis.Redis, Depends(get_redis)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> CreateRoomResponse:
    client_ip = get_client_ip(request, settings.trusted_proxies)
    await _enforce_rate_limit(
        redis_client,
        scope="create_ip",
        key=client_ip,
        limit=settings.rate_limit_create_room_per_hour,
        window_seconds=60 * 60,
    )

    room = await room_service.create_room()
    logger.info("room_created", room_id=room.room_id, nameplate=room.nameplate)
    return CreateRoomResponse(
        room_id=room.room_id,
        code=room.code,
        nameplate=room.nameplate,
        expires_at=room.expires_at,
        token=room.token,
    )


@router.post("/join", response_model=JoinRoomResponse)
async def join_room(
    request: Request,
    body: JoinRoomRequest,
    room_service: Annotated[RoomService, Depends(get_room_service)],
    redis_client: Annotated[redis.Redis, Depends(get_redis)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> JoinRoomResponse:
    client_ip = get_client_ip(request, settings.trusted_proxies)
    await _enforce_rate_limit(
        redis_client,
        scope="join_ip",
        key=client_ip,
        limit=settings.rate_limit_join_per_minute,
        window_seconds=60,
    )

    # The per-nameplate limit (docs/data-model.md's `rl:join_nameplate:*` scope) is
    # applied inside the service, once the code has been parsed into a nameplate --
    # it can't be a route-level dependency since the nameplate lives in the body.
    joined = await room_service.join_room(body.code)

    logger.info("room_joined", room_id=joined.room_id)
    return JoinRoomResponse(
        room_id=joined.room_id,
        expires_at=joined.expires_at,
        token=joined.token,
    )


@router.delete("/{room_id}", status_code=204)
async def close_room(
    room_id: str,
    room_service: Annotated[RoomService, Depends(get_room_service)],
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    token = extract_bearer_token(authorization)
    claims = room_service.authorize(token, room_id=room_id)
    await room_service.close_room(room_id, claims)
    logger.info("room_closed", room_id=room_id, by_peer=claims.peer_id)
