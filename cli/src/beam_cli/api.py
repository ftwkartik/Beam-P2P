"""REST client for the room lifecycle endpoints (docs/protocol.md §2). Direct
counterpart of the web client's `engine/api.ts`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


class ApiError(Exception):
    """A REST call failed; `code`/`message` come from the server's error envelope
    (`{"error": {"code", "message"}}`, docs/security.md's "uniform error envelope")."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass(frozen=True, slots=True)
class CreateRoomResult:
    room_id: str
    code: str
    nameplate: int
    expires_at: float
    token: str


@dataclass(frozen=True, slots=True)
class JoinRoomResult:
    room_id: str
    expires_at: float
    token: str


@dataclass(frozen=True, slots=True)
class IceServer:
    urls: str
    username: str | None = None
    credential: str | None = None


def _raise_for_error(response: httpx.Response) -> None:
    if response.is_success:
        return
    try:
        body: Any = response.json()
    except ValueError:
        body = None
    envelope = (body or {}).get("error") if isinstance(body, dict) else None
    code = (envelope or {}).get("code", "UNKNOWN")
    message = (envelope or {}).get("message", "The request failed.")
    raise ApiError(response.status_code, code, message)


class ApiClient:
    """One instance per room: `base_url` is the server's origin (e.g.
    `http://localhost:8080`), and `signaling_ws_url()` derives the matching `/ws` URL.

    `transport` is injectable so tests can talk to a real `beam_server` app in-process
    over `httpx.ASGITransport` instead of a real socket (docs/adr/007's "injectable
    factories" pattern, applied here to the CLI's own REST client).
    """

    def __init__(self, base_url: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self._base_url, transport=self._transport)

    def signaling_ws_url(self) -> str:
        scheme = "wss" if self._base_url.startswith("https://") else "ws"
        origin = self._base_url.split("://", 1)[-1]
        return f"{scheme}://{origin}/ws"

    async def create_room(self) -> CreateRoomResult:
        async with self._client() as client:
            response = await client.post("/api/v1/rooms")
        _raise_for_error(response)
        return CreateRoomResult(**response.json())

    async def join_room(self, code: str) -> JoinRoomResult:
        async with self._client() as client:
            response = await client.post("/api/v1/rooms/join", json={"code": code})
        _raise_for_error(response)
        return JoinRoomResult(**response.json())

    async def get_ice_servers(self, room_id: str, token: str) -> list[IceServer]:
        async with self._client() as client:
            response = await client.get(
                f"/api/v1/rooms/{room_id}/ice-servers",
                headers={"Authorization": f"Bearer {token}"},
            )
        _raise_for_error(response)
        return [IceServer(**s) for s in response.json()["ice_servers"]]

    async def close_room(self, room_id: str, token: str) -> None:
        async with self._client() as client:
            response = await client.delete(
                f"/api/v1/rooms/{room_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
        _raise_for_error(response)
