"""Signaling WebSocket integration tests (docs/protocol.md §3;
docs/testing-strategy.md's "Signaling" inventory).

Uses Starlette's `TestClient` (sync, thread-backed) for both the REST room endpoints
and the `/ws` connections in the same test -- it runs the real ASGI app, so two
connections can be held open at once without extra async plumbing.

**Why fakeredis here, unlike test_rooms.py:** the `/ws` endpoint itself never touches
Lua (only `POST /rooms/join`'s atomic accept-the-join step does, and that's already
covered against real Redis in test_rooms.py). A real `redis.asyncio.Redis` client was
also found, empirically, to hang unpredictably when two *concurrent* WebSocket
connections each drive their own real Redis client under this specific
TestClient-in-a-background-thread setup (a redis-py/anyio interaction this project
doesn't need to chase further, since the WS layer has no Lua dependency to justify real
Redis anyway). fakeredis with one shared `FakeServer` avoids it entirely and is faster.
Because fakeredis doesn't implement EVAL/EVALSHA, room joins here are set up directly
against the fake store (`_simulate_join`) instead of calling `POST /rooms/join`.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from collections.abc import AsyncIterator, Iterator

import fakeredis
import fakeredis.aioredis
import pytest
import redis.asyncio as redis
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from beam_protocol.constants import MAX_SIGNALING_MESSAGE_BYTES
from beam_server.config import Settings, get_settings
from beam_server.main import create_app
from beam_server.security.tokens import create_room_token
from beam_server.store.redis import get_redis

_JWT_SECRET = "test-jwt-secret-at-least-32-bytes-long"  # pragma: allowlist secret
_CODE_PEPPER = "test-code-pepper-at-least-32-bytes-long"  # pragma: allowlist secret


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "env": "test",
        "jwt_secret": _JWT_SECRET,
        "code_pepper": _CODE_PEPPER,
        "allowed_origins": ["http://allowed.example"],
        "ws_hello_timeout_seconds": 0.3,
        "ws_reconnect_grace_seconds": 2.0,
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


class ClientFactory:
    """Callable fixture object: `make_client()` builds a TestClient; every client
    built by the *same* factory instance shares one in-memory fake Redis server, so a
    room created through one client is visible to another.
    """

    def __init__(self) -> None:
        self.fake_server = fakeredis.FakeServer()
        self._clients: list[TestClient] = []

    def __call__(self, **settings_overrides: object) -> TestClient:
        settings = _settings(**settings_overrides)
        app: FastAPI = create_app(
            settings=settings,
            # Without this, the app's pub/sub relay (created in the lifespan; see
            # beam_server.main) would default to a real redis.asyncio client pointed
            # at Settings.redis_url and fail to connect -- only the per-request
            # get_redis dependency below is overridden by default.
            redis_client_factory=lambda _url: fakeredis.aioredis.FakeRedis(
                server=self.fake_server, decode_responses=True
            ),
        )
        app.dependency_overrides[get_settings] = lambda: settings

        async def _override_get_redis() -> AsyncIterator[redis.Redis]:
            client = fakeredis.aioredis.FakeRedis(server=self.fake_server, decode_responses=True)
            try:
                yield client
            finally:
                await client.aclose()

        app.dependency_overrides[get_redis] = _override_get_redis

        client = TestClient(app)
        client.__enter__()  # runs the ASGI lifespan
        self._clients.append(client)
        return client

    def close(self) -> None:
        for client in self._clients:
            client.__exit__(None, None, None)


@pytest.fixture
def make_client() -> Iterator[ClientFactory]:
    factory = ClientFactory()
    yield factory
    factory.close()


def _create_room(client: TestClient) -> dict:
    response = client.post("/api/v1/rooms")
    assert response.status_code == 201, response.text
    return response.json()


def _simulate_join(make_client_fn: ClientFactory, room: dict) -> dict:
    """Marks `room` as joined directly in the fake Redis store and mints a matching
    joiner token, bypassing the (Lua-backed) `POST /rooms/join` endpoint. Shaped like
    that endpoint's JoinRoomResponse, plus `peer_id` for test convenience.
    """
    peer_id = secrets.token_urlsafe(16)

    async def _apply() -> None:
        client = fakeredis.aioredis.FakeRedis(
            server=make_client_fn.fake_server, decode_responses=True
        )
        try:
            await client.hset(
                f"room:{room['room_id']}",
                mapping={"joiner_peer": peer_id, "state": "paired"},
            )
        finally:
            await client.aclose()

    asyncio.run(_apply())

    token = create_room_token(
        secret=_JWT_SECRET,
        peer_id=peer_id,
        room_id=room["room_id"],
        role="joiner",
        ttl_seconds=3600,
    )
    return {
        "room_id": room["room_id"],
        "expires_at": room["expires_at"],
        "token": token,
        "peer_id": peer_id,
    }


async def _get_presence(make_client_fn: ClientFactory, room_id: str, peer_id: str) -> str | None:
    """Read a peer's presence state directly from the shared fake store."""
    client = fakeredis.aioredis.FakeRedis(server=make_client_fn.fake_server, decode_responses=True)
    try:
        return await client.hget(f"room:{room_id}:peer:{peer_id}", "state")
    finally:
        await client.aclose()


def _set_presence_online(make_client_fn: ClientFactory, room_id: str, peer_id: str) -> None:
    """Write a peer's presence to "online" directly, the way `PresenceRepository.
    set_online` (called from a real connect flow, possibly on a different instance)
    would -- used to simulate a cross-instance reconnect without a second live
    connection (see `TestMultiInstance`'s note on why).
    """

    async def _apply() -> None:
        client = fakeredis.aioredis.FakeRedis(
            server=make_client_fn.fake_server, decode_responses=True
        )
        try:
            await client.hset(f"room:{room_id}:peer:{peer_id}", "state", "online")
        finally:
            await client.aclose()

    asyncio.run(_apply())


def _hello(token: str, *, kind: str = "web", version: str = "1.0.0") -> dict:
    return {"type": "hello", "token": token, "client": {"kind": kind, "version": version}}


def _candidate_signal(text: str = "c") -> dict:
    return {
        "type": "signal",
        "data": {
            "kind": "candidate",
            "candidate": {
                "candidate": text,
                "sdpMid": None,
                "sdpMLineIndex": None,
                "usernameFragment": None,
            },
        },
    }


class TestHelloAndAuth:
    def test_valid_hello_gets_welcome(self, make_client: ClientFactory) -> None:
        client = make_client()
        room = _create_room(client)

        with client.websocket_connect("/ws", headers={"origin": "http://allowed.example"}) as ws:
            ws.send_json(_hello(room["token"]))
            welcome = ws.receive_json()

        assert welcome["type"] == "welcome"
        assert welcome["room_id"] == room["room_id"]
        assert welcome["role"] == "creator"
        assert welcome["polite"] is False
        assert welcome["peers"] == []

    def test_joiner_is_polite(self, make_client: ClientFactory) -> None:
        client = make_client()
        room = _create_room(client)
        joined = _simulate_join(make_client, room)

        with client.websocket_connect("/ws") as ws:
            ws.send_json(_hello(joined["token"]))
            welcome = ws.receive_json()

        assert welcome["role"] == "joiner"
        assert welcome["polite"] is True

    def test_no_hello_within_timeout_closes(self, make_client: ClientFactory) -> None:
        client = make_client(ws_hello_timeout_seconds=0.2)
        with (
            pytest.raises(WebSocketDisconnect) as exc_info,
            client.websocket_connect("/ws") as ws,
        ):
            ws.receive_json()
        assert exc_info.value.code == 4408

    def test_disallowed_origin_is_rejected(self, make_client: ClientFactory) -> None:
        client = make_client()
        with (
            pytest.raises(WebSocketDisconnect) as exc_info,
            client.websocket_connect("/ws", headers={"origin": "http://evil.example"}),
        ):
            pass
        assert exc_info.value.code == 4403

    def test_no_origin_header_is_allowed(self, make_client: ClientFactory) -> None:
        """A CLI client sends no Origin header at all; it must not be rejected."""
        client = make_client()
        room = _create_room(client)
        with client.websocket_connect("/ws") as ws:
            ws.send_json(_hello(room["token"]))
            welcome = ws.receive_json()
        assert welcome["type"] == "welcome"

    def test_malformed_first_message_closes(self, make_client: ClientFactory) -> None:
        client = make_client()
        with (
            pytest.raises(WebSocketDisconnect) as exc_info,
            client.websocket_connect("/ws") as ws,
        ):
            ws.send_json({"type": "not-a-real-type"})
            ws.receive_json()
        assert exc_info.value.code == 4400

    def test_non_hello_first_message_closes(self, make_client: ClientFactory) -> None:
        client = make_client()
        with (
            pytest.raises(WebSocketDisconnect) as exc_info,
            client.websocket_connect("/ws") as ws,
        ):
            ws.send_json({"type": "leave"})
            ws.receive_json()
        assert exc_info.value.code == 4400

    def test_invalid_token_closes(self, make_client: ClientFactory) -> None:
        client = make_client()
        with (
            pytest.raises(WebSocketDisconnect) as exc_info,
            client.websocket_connect("/ws") as ws,
        ):
            ws.send_json(_hello("not-a-real-token"))
            ws.receive_json()
        assert exc_info.value.code == 4401

    def test_token_for_nonexistent_room_closes(self, make_client: ClientFactory) -> None:
        client = make_client()
        token = create_room_token(
            secret=_JWT_SECRET,
            peer_id="ghost-peer",
            room_id="room-that-does-not-exist",
            role="creator",
            ttl_seconds=60,
        )
        with (
            pytest.raises(WebSocketDisconnect) as exc_info,
            client.websocket_connect("/ws") as ws,
        ):
            ws.send_json(_hello(token))
            ws.receive_json()
        assert exc_info.value.code == 4404

    def test_token_for_peer_not_in_the_room_closes(self, make_client: ClientFactory) -> None:
        client = make_client()
        room = _create_room(client)
        # Nobody has joined yet, so no peer_id is assigned to the "joiner" role --
        # any joiner token is invalid until a real join happens.
        forged = create_room_token(
            secret=_JWT_SECRET,
            peer_id="someone-who-never-joined",
            room_id=room["room_id"],
            role="joiner",
            ttl_seconds=60,
        )
        with (
            pytest.raises(WebSocketDisconnect) as exc_info,
            client.websocket_connect("/ws") as ws,
        ):
            ws.send_json(_hello(forged))
            ws.receive_json()
        assert exc_info.value.code == 4401

    def test_second_connection_for_same_peer_replaces_the_first(
        self, make_client: ClientFactory
    ) -> None:
        client = make_client()
        room = _create_room(client)

        with client.websocket_connect("/ws") as first:
            first.send_json(_hello(room["token"]))
            first.receive_json()  # welcome

            with client.websocket_connect("/ws") as second:
                second.send_json(_hello(room["token"]))
                second.receive_json()  # welcome

                with pytest.raises(WebSocketDisconnect) as exc_info:
                    first.receive_json()
                assert exc_info.value.code == 4409


class TestRelayAndPresence:
    def test_peer_joined_and_signal_relay(self, make_client: ClientFactory) -> None:
        client = make_client()
        room = _create_room(client)
        joined = _simulate_join(make_client, room)

        with client.websocket_connect("/ws") as creator_ws:
            creator_ws.send_json(_hello(room["token"]))
            creator_welcome = creator_ws.receive_json()
            assert creator_welcome["peers"] == []

            with client.websocket_connect("/ws") as joiner_ws:
                joiner_ws.send_json(_hello(joined["token"]))
                joiner_welcome = joiner_ws.receive_json()
                assert joiner_welcome["peers"] == [
                    {
                        "peer_id": creator_welcome["peer_id"],
                        "role": "creator",
                        "state": "online",
                    }
                ]

                peer_joined = creator_ws.receive_json()
                assert peer_joined == {
                    "type": "peer_joined",
                    "peer_id": joiner_welcome["peer_id"],
                    "role": "joiner",
                }

                joiner_ws.send_json(
                    {
                        "type": "signal",
                        "data": {
                            "kind": "description",
                            "description": {"type": "offer", "sdp": "v=0"},
                        },
                    }
                )
                relayed = creator_ws.receive_json()
                assert relayed["type"] == "signal"
                assert relayed["from"] == joiner_welcome["peer_id"]
                assert relayed["data"]["description"]["sdp"] == "v=0"

    def test_relay_works_in_both_directions(self, make_client: ClientFactory) -> None:
        client = make_client()
        room = _create_room(client)
        joined = _simulate_join(make_client, room)

        with client.websocket_connect("/ws") as creator_ws:
            creator_ws.send_json(_hello(room["token"]))
            creator_welcome = creator_ws.receive_json()

            with client.websocket_connect("/ws") as joiner_ws:
                joiner_ws.send_json(_hello(joined["token"]))
                joiner_ws.receive_json()  # welcome
                creator_ws.receive_json()  # peer_joined

                creator_ws.send_json(_candidate_signal("from-creator"))
                relayed = joiner_ws.receive_json()
                assert relayed["from"] == creator_welcome["peer_id"]
                assert relayed["data"]["candidate"]["candidate"] == "from-creator"

    def test_leave_notifies_the_other_peer_immediately(self, make_client: ClientFactory) -> None:
        client = make_client()
        room = _create_room(client)
        joined = _simulate_join(make_client, room)

        with client.websocket_connect("/ws") as creator_ws:
            creator_ws.send_json(_hello(room["token"]))
            creator_ws.receive_json()  # welcome

            with client.websocket_connect("/ws") as joiner_ws:
                joiner_ws.send_json(_hello(joined["token"]))
                joiner_welcome = joiner_ws.receive_json()
                creator_ws.receive_json()  # peer_joined

                joiner_ws.send_json({"type": "leave"})
                # Wait for the server to actually process the leave (and notify the
                # other peer) before this `with` block's __exit__ tears the socket
                # down from the client side too.
                with pytest.raises(WebSocketDisconnect):
                    joiner_ws.receive_json()

            left = creator_ws.receive_json()
            assert left == {
                "type": "peer_left",
                "peer_id": joiner_welcome["peer_id"],
                "reason": "left",
            }

    def test_disconnect_then_prompt_reconnect_sends_peer_joined_again(
        self, make_client: ClientFactory
    ) -> None:
        client = make_client(ws_reconnect_grace_seconds=5.0)
        room = _create_room(client)
        joined = _simulate_join(make_client, room)

        with client.websocket_connect("/ws") as creator_ws:
            creator_ws.send_json(_hello(room["token"]))
            creator_ws.receive_json()  # welcome

            with client.websocket_connect("/ws") as joiner_ws:
                joiner_ws.send_json(_hello(joined["token"]))
                joiner_welcome = joiner_ws.receive_json()
                creator_ws.receive_json()  # peer_joined
            # `with` exits here: the joiner's socket closes WITHOUT sending `leave`,
            # an ordinary disconnect.

            joiner_peer_id = joiner_welcome["peer_id"]
            reconnecting = creator_ws.receive_json()
            assert reconnecting == {"type": "peer_reconnecting", "peer_id": joiner_peer_id}

            # Reconnect well within the 5s grace window.
            with client.websocket_connect("/ws") as joiner_ws_2:
                joiner_ws_2.send_json(_hello(joined["token"]))
                joiner_ws_2.receive_json()  # welcome

                rejoined = creator_ws.receive_json()
                assert rejoined == {
                    "type": "peer_joined",
                    "peer_id": joiner_peer_id,
                    "role": "joiner",
                }

    def test_disconnect_without_reconnect_eventually_sends_peer_left_timeout(
        self, make_client: ClientFactory
    ) -> None:
        client = make_client(ws_reconnect_grace_seconds=0.3)
        room = _create_room(client)
        joined = _simulate_join(make_client, room)

        with client.websocket_connect("/ws") as creator_ws:
            creator_ws.send_json(_hello(room["token"]))
            creator_ws.receive_json()  # welcome

            with client.websocket_connect("/ws") as joiner_ws:
                joiner_ws.send_json(_hello(joined["token"]))
                joiner_welcome = joiner_ws.receive_json()
                creator_ws.receive_json()  # peer_joined

            creator_ws.receive_json()  # peer_reconnecting

            time.sleep(0.6)  # past the 0.3s grace window

            left = creator_ws.receive_json()
            assert left == {
                "type": "peer_left",
                "peer_id": joiner_welcome["peer_id"],
                "reason": "timeout",
            }


class TestLimits:
    def test_oversized_message_closes(self, make_client: ClientFactory) -> None:
        client = make_client()

        with (
            pytest.raises(WebSocketDisconnect) as exc_info,
            client.websocket_connect("/ws") as ws,
        ):
            ws.send_text("x" * (MAX_SIGNALING_MESSAGE_BYTES + 1))
            ws.receive_json()
        assert exc_info.value.code == 4413

    def test_message_rate_limit_closes(self, make_client: ClientFactory) -> None:
        client = make_client(ws_message_rate_per_second=1.0, ws_message_burst=1)
        room = _create_room(client)

        with (
            pytest.raises(WebSocketDisconnect) as exc_info,
            client.websocket_connect("/ws") as ws,
        ):
            ws.send_json(_hello(room["token"]))
            ws.receive_json()  # welcome (consumes no token; hello is pre-loop)
            ws.send_json(_candidate_signal("1"))  # spends the single burst token
            ws.send_json(_candidate_signal("2"))  # over the limit
            ws.receive_json()
        assert exc_info.value.code == 4429

    def test_too_many_ice_candidates_closes(self, make_client: ClientFactory) -> None:
        client = make_client(ws_max_ice_candidates_per_session=2)
        room = _create_room(client)

        with (
            pytest.raises(WebSocketDisconnect) as exc_info,
            client.websocket_connect("/ws") as ws,
        ):
            ws.send_json(_hello(room["token"]))
            ws.receive_json()  # welcome
            for i in range(3):
                ws.send_json(_candidate_signal(str(i)))
            ws.receive_json()
        assert exc_info.value.code == 4400


class TestMultiInstance:
    """The actual Milestone 5 property: two peers of the same room, each connected to
    a *different* server instance, can still signal each other. `make_client()`
    already builds a fresh `create_app()` (its own hub, pub/sub task and instance ID)
    per call, sharing only the fake Redis *server* between them -- exactly analogous to
    two real processes sharing one Redis.
    """

    def test_signal_relays_across_two_instances(self, make_client: ClientFactory) -> None:
        instance_a = make_client()
        instance_b = make_client()

        room = _create_room(instance_a)
        joined = _simulate_join(make_client, room)

        with instance_a.websocket_connect("/ws") as creator_ws:
            creator_ws.send_json(_hello(room["token"]))
            creator_welcome = creator_ws.receive_json()
            assert creator_welcome["peers"] == []

            with instance_b.websocket_connect("/ws") as joiner_ws:
                joiner_ws.send_json(_hello(joined["token"]))
                joiner_welcome = joiner_ws.receive_json()
                # Presence is Redis-backed and shared, so instance B correctly reports
                # the creator as online even though B has never seen that connection.
                assert joiner_welcome["peers"] == [
                    {
                        "peer_id": creator_welcome["peer_id"],
                        "role": "creator",
                        "state": "online",
                    }
                ]

                # Relayed instance B -> A over Redis pub/sub.
                peer_joined = creator_ws.receive_json()
                assert peer_joined == {
                    "type": "peer_joined",
                    "peer_id": joiner_welcome["peer_id"],
                    "role": "joiner",
                }

                joiner_ws.send_json(_candidate_signal("cross-instance"))
                relayed = creator_ws.receive_json()
                assert relayed["type"] == "signal"
                assert relayed["from"] == joiner_welcome["peer_id"]
                assert relayed["data"]["candidate"]["candidate"] == "cross-instance"

                # And the other direction, A -> B.
                creator_ws.send_json(_candidate_signal("back-to-b"))
                relayed_back = joiner_ws.receive_json()
                assert relayed_back["from"] == creator_welcome["peer_id"]
                assert relayed_back["data"]["candidate"]["candidate"] == "back-to-b"

    def test_leave_relays_across_two_instances(self, make_client: ClientFactory) -> None:
        instance_a = make_client()
        instance_b = make_client()

        room = _create_room(instance_a)
        joined = _simulate_join(make_client, room)

        with instance_a.websocket_connect("/ws") as creator_ws:
            creator_ws.send_json(_hello(room["token"]))
            creator_ws.receive_json()  # welcome

            with instance_b.websocket_connect("/ws") as joiner_ws:
                joiner_ws.send_json(_hello(joined["token"]))
                joiner_welcome = joiner_ws.receive_json()
                creator_ws.receive_json()  # peer_joined

                joiner_ws.send_json({"type": "leave"})
                with pytest.raises(WebSocketDisconnect):
                    joiner_ws.receive_json()

            left = creator_ws.receive_json()
            assert left == {
                "type": "peer_left",
                "peer_id": joiner_welcome["peer_id"],
                "reason": "left",
            }

    # A genuine cross-instance reconnect -- disconnect from instance B, reconnect on
    # instance A -- is *not* exercised here as a live two-TestClient scenario. Doing
    # so reliably triggered an indefinite hang in this exact test harness (two
    # Starlette TestClients, each on its own background thread, one of them handling
    # a disconnect at the moment the other reconnects); it reproduced consistently but
    # its root cause was not conclusively identified even after ruling out this
    # project's own subscribe/unsubscribe logic (isolated repros of that in plain
    # asyncio, without TestClient, behaved correctly). Rather than leave a test that
    # hangs CI, the property this would have covered -- the stale-reconnect-timer
    # correctness fix in ws/endpoint.py's `_on_reconnect_timeout` -- is instead
    # verified below by directly manipulating presence the way a *different*
    # instance's own connect flow would, which exercises the same code path without
    # needing a second live TestClient. It was also confirmed manually end to end
    # against two real `uvicorn` processes sharing one real Redis (see the Milestone 5
    # commit message).
    def test_stale_reconnect_timer_backs_off_once_presence_shows_online(
        self, make_client: ClientFactory
    ) -> None:
        client = make_client(ws_reconnect_grace_seconds=0.3)
        room = _create_room(client)
        joined = _simulate_join(make_client, room)

        with client.websocket_connect("/ws") as creator_ws:
            creator_ws.send_json(_hello(room["token"]))
            creator_ws.receive_json()  # welcome

            with client.websocket_connect("/ws") as joiner_ws:
                joiner_ws.send_json(_hello(joined["token"]))
                joiner_ws.receive_json()  # welcome
                creator_ws.receive_json()  # peer_joined
            # joiner disconnects; this instance schedules its own reconnect timer.

            creator_ws.receive_json()  # peer_reconnecting

            # Simulate a *different* instance's connect flow having already run for
            # this same peer (PresenceRepository.set_online) -- exactly what a real
            # cross-instance reconnect would have produced by this point.
            _set_presence_online(make_client, room["room_id"], joined["peer_id"])

            # Wait past the grace period. The stale timer fires now, but must see
            # (via shared presence) that the peer is back and do nothing.
            time.sleep(0.6)
            presence = asyncio.run(_get_presence(make_client, room["room_id"], joined["peer_id"]))
            assert presence == "online"
