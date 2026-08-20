"""ICE server credentials (docs/protocol.md §2).

STUN only for now; ephemeral TURN credentials are added in Milestone 6 once coturn is
part of the stack (docs/adr/004-turn-coturn-ephemeral-credentials.md). This lives in
its own file (rather than api/rooms.py) because it will grow non-trivial TURN
credential-minting logic later, separate from room lifecycle concerns.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel

from beam_server.api.rooms import extract_bearer_token, get_room_service
from beam_server.config import Settings, get_settings
from beam_server.services.rooms import RoomService

router = APIRouter(prefix="/rooms", tags=["ice"])


class IceServer(BaseModel):
    urls: str


class IceServersResponse(BaseModel):
    ice_servers: list[IceServer]


@router.get("/{room_id}/ice-servers", response_model=IceServersResponse)
async def get_ice_servers(
    room_id: str,
    room_service: Annotated[RoomService, Depends(get_room_service)],
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> IceServersResponse:
    token = extract_bearer_token(authorization)
    room_service.authorize(token, room_id=room_id)

    return IceServersResponse(ice_servers=[IceServer(urls=url) for url in settings.stun_servers])
