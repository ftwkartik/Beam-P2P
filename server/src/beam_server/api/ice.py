"""ICE server credentials (docs/protocol.md §2).

STUN is always offered; TURN URLs plus a freshly minted ephemeral credential
(docs/adr/004-turn-coturn-ephemeral-credentials.md) are added whenever
`settings.turn_urls` is non-empty (compose configures it; a bare `stun_servers`-only
deployment simply omits coturn). This lives in its own file rather than api/rooms.py
because it has its own rate-limit scope and will grow further TURN-specific concerns.
"""

from __future__ import annotations

from typing import Annotated

import redis.asyncio as redis
from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel

from beam_server.api.rooms import enforce_rate_limit, extract_bearer_token, get_room_service
from beam_server.config import Settings, get_settings
from beam_server.observability.metrics import turn_credentials_issued_total
from beam_server.services.rooms import RoomService
from beam_server.services.turn import mint_turn_credentials
from beam_server.store.redis import get_redis

router = APIRouter(prefix="/rooms", tags=["ice"])


class IceServer(BaseModel):
    urls: str
    username: str | None = None
    credential: str | None = None


class IceServersResponse(BaseModel):
    ice_servers: list[IceServer]


@router.get("/{room_id}/ice-servers", response_model=IceServersResponse)
async def get_ice_servers(
    room_id: str,
    room_service: Annotated[RoomService, Depends(get_room_service)],
    redis_client: Annotated[redis.Redis, Depends(get_redis)],
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> IceServersResponse:
    token = extract_bearer_token(authorization)
    claims = room_service.authorize(token, room_id=room_id)

    await enforce_rate_limit(
        redis_client,
        scope="ice_servers_room",
        key=room_id,
        limit=settings.rate_limit_ice_servers_per_room_per_minute,
        window_seconds=60,
    )

    ice_servers = [IceServer(urls=url) for url in settings.stun_servers]

    if settings.turn_urls:
        creds = mint_turn_credentials(
            secret=settings.turn_secret.get_secret_value(),
            peer_id=claims.peer_id,
            ttl_seconds=settings.turn_credential_ttl_seconds,
            urls=settings.turn_urls,
        )
        turn_credentials_issued_total.inc()
        ice_servers.extend(
            IceServer(urls=url, username=creds.username, credential=creds.credential)
            for url in creds.urls
        )

    return IceServersResponse(ice_servers=ice_servers)
