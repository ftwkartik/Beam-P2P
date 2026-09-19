"""SignalingClient against a real beam_server app over a real WebSocket (needed here,
unlike api.py's tests: `websockets.connect()` needs an actual socket, not an ASGI
transport) -- a real uvicorn server on an ephemeral port. Rooms are created through
the real REST API (not a hand-crafted token for a room that doesn't exist in the
store), exactly as the real CLI does; that includes `join_room`, whose atomic-join Lua
script fakeredis can't run, so this whole file needs a real Redis (see conftest.py's
`real_redis`), same as test_api.py's join tests.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
import redis.asyncio as redis
import uvicorn
from fastapi import FastAPI

from beam_cli.api import ApiClient
from beam_cli.signaling_client import SignalingCallbacks, SignalingClient
from beam_protocol.signaling import IceSignal, PeerJoinedMessage, WelcomeMessage
from beam_server.config import Settings, get_settings
from beam_server.main import create_app
from beam_server.security.tokens import create_room_token
from beam_server.store.redis import get_redis

_JWT_SECRET = "test-jwt-secret-at-least-32-bytes-long"  # pragma: allowlist secret
_CODE_PEPPER = "test-code-pepper-at-least-32-bytes-long"  # pragma: allowlist secret


async def _run_server(app: FastAPI, port: int) -> uvicorn.Server:
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
        if task.done():
            task.result()  # re-raise if startup failed
    return server


@pytest.fixture
async def live_server(
    unused_tcp_port: int, real_redis: redis.Redis
) -> AsyncIterator[tuple[str, ApiClient]]:
    settings = Settings(env="test", jwt_secret=_JWT_SECRET, code_pepper=_CODE_PEPPER)
    app = create_app(settings=settings, redis_client_factory=lambda _url: real_redis)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_redis] = lambda: real_redis

    server = await _run_server(app, unused_tcp_port)
    base_url = f"http://127.0.0.1:{unused_tcp_port}"
    try:
        yield f"ws://127.0.0.1:{unused_tcp_port}/ws", ApiClient(base_url)
    finally:
        server.should_exit = True
        await asyncio.sleep(0.05)


async def test_hello_gets_a_welcome(live_server: tuple[str, ApiClient]) -> None:
    ws_url, api = live_server
    room = await api.create_room()

    welcomes: list[WelcomeMessage] = []
    client = SignalingClient(
        ws_url=ws_url,
        token=room.token,
        callbacks=SignalingCallbacks(on_welcome=welcomes.append),
    )
    await client.connect()
    await asyncio.sleep(0.1)

    assert len(welcomes) == 1
    assert welcomes[0].role == "creator"
    assert welcomes[0].peers == []

    await client.leave()


async def test_two_peers_see_each_other_join_and_relay_a_signal(
    live_server: tuple[str, ApiClient],
) -> None:
    ws_url, api = live_server
    room = await api.create_room()

    joined_events: list[PeerJoinedMessage] = []
    signals: list[tuple[str, object]] = []

    creator = SignalingClient(
        ws_url=ws_url,
        token=room.token,
        callbacks=SignalingCallbacks(
            on_peer_joined=joined_events.append,
            on_signal=lambda frm, data: signals.append((frm, data)),
        ),
    )
    await creator.connect()
    await asyncio.sleep(0.1)

    joined = await api.join_room(room.code)
    joiner = SignalingClient(ws_url=ws_url, token=joined.token)
    await joiner.connect()
    await asyncio.sleep(0.1)

    assert len(joined_events) == 1

    await joiner.send_signal(IceSignal(candidate=None))
    await asyncio.sleep(0.1)

    assert len(signals) == 1
    frm, data = signals[0]
    assert frm == joined_events[0].peer_id
    assert isinstance(data, IceSignal)
    assert data.candidate is None

    await creator.leave()
    await joiner.leave()


async def test_invalid_token_is_fatal(live_server: tuple[str, ApiClient]) -> None:
    ws_url, _api = live_server
    fatal_messages: list[str] = []
    client = SignalingClient(
        ws_url=ws_url,
        token="not-a-real-token",
        callbacks=SignalingCallbacks(on_fatal=fatal_messages.append),
    )
    await client.connect()
    await asyncio.wait_for(client.wait_closed(), timeout=5)

    assert len(fatal_messages) == 1


async def test_token_for_a_nonexistent_room_is_fatal(live_server: tuple[str, ApiClient]) -> None:
    ws_url, _api = live_server
    fatal_messages: list[str] = []
    token = create_room_token(
        secret=_JWT_SECRET,
        peer_id="peer-a",
        room_id="no-such-room",
        role="creator",
        ttl_seconds=3600,
    )
    client = SignalingClient(
        ws_url=ws_url,
        token=token,
        callbacks=SignalingCallbacks(on_fatal=fatal_messages.append),
    )
    await client.connect()
    await asyncio.wait_for(client.wait_closed(), timeout=5)

    assert len(fatal_messages) == 1
