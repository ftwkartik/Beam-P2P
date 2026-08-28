"""The `/ws` signaling endpoint (docs/protocol.md §3).

One WebSocket connection is exactly one peer's session in exactly one room. The
handshake (`hello` -> token check -> room lookup -> `welcome`) all happens before
anything is registered anywhere; only a fully authenticated connection becomes visible
to the other peer or to presence.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime
from typing import Annotated

import redis.asyncio as redis
import structlog
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from pydantic import TypeAdapter, ValidationError

from beam_protocol.constants import (
    MAX_SIGNALING_MESSAGE_BYTES,
    WS_CLOSE_HELLO_TIMEOUT,
    WS_CLOSE_MALFORMED_MESSAGE,
    WS_CLOSE_MESSAGE_TOO_LARGE,
    WS_CLOSE_ORIGIN_NOT_ALLOWED,
    WS_CLOSE_RATE_LIMITED,
    WS_CLOSE_ROOM_NOT_FOUND,
    WS_CLOSE_SESSION_REPLACED,
    WS_CLOSE_UNAUTHORIZED,
)
from beam_protocol.signaling import (
    ClientMessage,
    ClientSignalMessage,
    HelloMessage,
    LeaveMessage,
    PeerInfo,
    PeerJoinedMessage,
    PeerLeftMessage,
    PeerReconnectingMessage,
    ServerSignalMessage,
    WelcomeMessage,
)
from beam_server.config import Settings, get_settings
from beam_server.observability.metrics import (
    signal_messages_relayed_total,
    ws_close_total,
    ws_connections_active,
    ws_connections_total,
)
from beam_server.security.origin import is_origin_allowed
from beam_server.security.tokens import Role, RoomTokenClaims, TokenError, decode_room_token
from beam_server.services.signaling import SignalingHub
from beam_server.store.presence_repo import PresenceRepository
from beam_server.store.pubsub import RoomPubSub
from beam_server.store.redis import get_redis
from beam_server.store.rooms_repo import RoomRecord, RoomsRepository
from beam_server.ws.session import PeerSession, TokenBucket

logger = structlog.get_logger(__name__)

router = APIRouter()

_client_message_adapter: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)


def get_signaling_hub(websocket: WebSocket) -> SignalingHub:
    hub: SignalingHub = websocket.app.state.signaling_hub
    return hub


def get_instance_id(websocket: WebSocket) -> str:
    instance_id: str = websocket.app.state.instance_id
    return instance_id


def get_pubsub(websocket: WebSocket) -> RoomPubSub:
    pubsub: RoomPubSub = websocket.app.state.pubsub
    return pubsub


def _other_peer_id(room: RoomRecord, claims: RoomTokenClaims) -> str | None:
    return room.joiner_peer if claims.role == "creator" else room.creator_peer


def _other_role(claims: RoomTokenClaims) -> Role:
    return "joiner" if claims.role == "creator" else "creator"


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=UTC).isoformat()


async def _receive_within(websocket: WebSocket, timeout: float) -> str:
    """Receive one text message within `timeout` seconds.

    Raises `TimeoutError` if nothing arrives in time, `WebSocketDisconnect` if the
    client disconnects instead, or another exception for a non-text frame.
    """
    return await asyncio.wait_for(websocket.receive_text(), timeout=timeout)


async def _close(websocket: WebSocket, code: int) -> None:
    """Close with `code` and record it -- every close in this module (other than a
    client-initiated disconnect) goes through here so `ws_close_total` stays accurate
    without scattering `.labels(...).inc()` calls through the control flow.
    """
    ws_close_total.labels(code=str(code)).inc()
    await websocket.close(code=code)


@router.websocket("/ws")
async def signaling_endpoint(
    websocket: WebSocket,
    redis_client: Annotated[redis.Redis, Depends(get_redis)],
    settings: Annotated[Settings, Depends(get_settings)],
    hub: Annotated[SignalingHub, Depends(get_signaling_hub)],
    instance_id: Annotated[str, Depends(get_instance_id)],
    pubsub: Annotated[RoomPubSub, Depends(get_pubsub)],
) -> None:
    origin = websocket.headers.get("origin")
    if not is_origin_allowed(origin, settings.allowed_origins):
        await _close(websocket, WS_CLOSE_ORIGIN_NOT_ALLOWED)
        return

    await websocket.accept()

    # --- hello --------------------------------------------------------------------
    try:
        raw = await _receive_within(websocket, settings.ws_hello_timeout_seconds)
    except TimeoutError:
        await _close(websocket, WS_CLOSE_HELLO_TIMEOUT)
        return
    except WebSocketDisconnect:
        return
    except Exception:  # a non-text frame, or anything else malformed at this stage
        await _close(websocket, WS_CLOSE_MALFORMED_MESSAGE)
        return

    if len(raw.encode("utf-8")) > MAX_SIGNALING_MESSAGE_BYTES:
        await _close(websocket, WS_CLOSE_MESSAGE_TOO_LARGE)
        return

    try:
        hello = _client_message_adapter.validate_json(raw)
    except ValidationError:
        await _close(websocket, WS_CLOSE_MALFORMED_MESSAGE)
        return
    if not isinstance(hello, HelloMessage):
        await _close(websocket, WS_CLOSE_MALFORMED_MESSAGE)
        return

    try:
        claims = decode_room_token(secret=settings.jwt_secret.get_secret_value(), token=hello.token)
    except TokenError:
        await _close(websocket, WS_CLOSE_UNAUTHORIZED)
        return

    rooms_repo = RoomsRepository(redis_client)
    room = await rooms_repo.get_room(claims.room_id)
    if room is None or room.state == "burned":
        await _close(websocket, WS_CLOSE_ROOM_NOT_FOUND)
        return

    expected_peer_id = room.creator_peer if claims.role == "creator" else room.joiner_peer
    if expected_peer_id != claims.peer_id:
        await _close(websocket, WS_CLOSE_UNAUTHORIZED)
        return

    # --- authenticated: register and announce --------------------------------------
    presence_repo = PresenceRepository(redis_client)

    previous = hub.get(claims.peer_id)
    if previous is not None:
        await _close(previous.websocket, WS_CLOSE_SESSION_REPLACED)
        hub.unregister(claims.peer_id, only_if=previous)

    was_reconnecting = hub.cancel_reconnect_timer(claims.peer_id)

    session = PeerSession(
        websocket=websocket,
        peer_id=claims.peer_id,
        room_id=claims.room_id,
        role=claims.role,
        client_kind=hello.client.kind,
        client_version=hello.client.version,
        rate_limiter=TokenBucket(
            rate_per_second=settings.ws_message_rate_per_second,
            burst=settings.ws_message_burst,
        ),
    )
    hub.register(session)
    ws_connections_total.inc()
    ws_connections_active.inc()
    await pubsub.subscribe_room(claims.room_id)
    await presence_repo.set_online(
        room_id=claims.room_id,
        peer_id=claims.peer_id,
        role=claims.role,
        instance_id=instance_id,
    )

    other_peer_id = _other_peer_id(room, claims)
    # Presence is the cross-instance-visible source of truth for "is the other peer
    # connected right now" -- unlike `hub`, which only ever knows about sessions on
    # *this* instance, and would wrongly report a peer on another instance as absent.
    other_presence = (
        await presence_repo.get(room_id=claims.room_id, peer_id=other_peer_id)
        if other_peer_id
        else None
    )

    peers = []
    if other_peer_id and other_presence is not None:
        peers.append(
            PeerInfo(
                peer_id=other_peer_id,
                role=_other_role(claims),
                state=other_presence.state,
            )
        )

    await websocket.send_text(
        WelcomeMessage(
            peer_id=claims.peer_id,
            room_id=claims.room_id,
            role=claims.role,
            polite=(claims.role == "joiner"),
            peers=peers,
            expires_at=_iso(room.expires_at),
        ).model_dump_json(by_alias=True)
    )

    if other_peer_id:
        # Whether this is a first connection or a return from a reconnect-grace
        # window, the other side is told the same way: this peer is here. Published
        # unconditionally -- we don't know or care which instance actually holds them.
        await pubsub.publish(
            claims.room_id,
            to=other_peer_id,
            message=PeerJoinedMessage(peer_id=claims.peer_id, role=claims.role),
        )

    logger.info(
        "ws_connected",
        room_id=claims.room_id,
        peer_id=claims.peer_id,
        role=claims.role,
        reconnect=was_reconnecting,
    )

    async def _on_reconnect_timeout() -> None:
        # The reconnect-grace timer is per-instance (an in-process asyncio task), but
        # the peer may have reconnected on a *different* instance in the meantime --
        # there's no direct way for that instance to reach across and cancel this
        # timer. Presence is Redis-backed and shared, though, and that other
        # instance's own connect flow already overwrote it to "online" (and already
        # told the other peer we're here); re-checking it here is what keeps a
        # same-instance and a cross-instance reconnect equally correct.
        current = await presence_repo.get(room_id=claims.room_id, peer_id=claims.peer_id)
        if current is not None and current.state == "online":
            logger.info(
                "ws_reconnect_timeout_stale", room_id=claims.room_id, peer_id=claims.peer_id
            )
            return

        await presence_repo.delete(room_id=claims.room_id, peer_id=claims.peer_id)
        if other_peer_id:
            await pubsub.publish(
                claims.room_id,
                to=other_peer_id,
                message=PeerLeftMessage(peer_id=claims.peer_id, reason="timeout"),
            )
        logger.info("ws_reconnect_grace_expired", room_id=claims.room_id, peer_id=claims.peer_id)

    # --- message loop ----------------------------------------------------------------
    graceful_leave = False
    try:
        while True:
            try:
                raw = await websocket.receive_text()
            except WebSocketDisconnect:
                break
            except Exception:
                await _close(websocket, WS_CLOSE_MALFORMED_MESSAGE)
                return

            if len(raw.encode("utf-8")) > MAX_SIGNALING_MESSAGE_BYTES:
                await _close(websocket, WS_CLOSE_MESSAGE_TOO_LARGE)
                return

            if not session.rate_limiter.try_consume():
                await _close(websocket, WS_CLOSE_RATE_LIMITED)
                return

            try:
                message = _client_message_adapter.validate_json(raw)
            except ValidationError:
                await _close(websocket, WS_CLOSE_MALFORMED_MESSAGE)
                return

            if isinstance(message, LeaveMessage):
                graceful_leave = True
                break

            if isinstance(message, ClientSignalMessage):
                if message.data.kind == "candidate" and message.data.candidate is not None:
                    session.ice_candidate_count += 1
                    if session.ice_candidate_count > settings.ws_max_ice_candidates_per_session:
                        await _close(websocket, WS_CLOSE_MALFORMED_MESSAGE)
                        return
                if other_peer_id:
                    await pubsub.publish(
                        claims.room_id,
                        to=other_peer_id,
                        # Constructed via model_validate rather than the constructor:
                        # "from" is the wire field name (see ServerSignalMessage's
                        # alias), and `from` is a Python keyword, so it can't be
                        # passed as `ServerSignalMessage(from=...)` syntactically.
                        message=ServerSignalMessage.model_validate(
                            {"from": claims.peer_id, "data": message.data}
                        ),
                    )
                    signal_messages_relayed_total.inc()
                continue

            # A second `hello` mid-session is a protocol violation.
            await _close(websocket, WS_CLOSE_MALFORMED_MESSAGE)
            return
    finally:
        # Was this session ever replaced by a newer one for the same peer ID (see the
        # 4409 handling above)? If so, that newer session has already told the other
        # peer we're here, has its own presence row, and owns the reconnect-grace
        # timer -- this (stale) session's cleanup must not undo any of that, or a
        # replaced-but-still-connected peer would spuriously look like it vanished.
        is_still_current = hub.get(claims.peer_id) is session
        hub.unregister(claims.peer_id, only_if=session)
        await pubsub.unsubscribe_room(claims.room_id)
        ws_connections_active.dec()

        if graceful_leave:
            await presence_repo.delete(room_id=claims.room_id, peer_id=claims.peer_id)
            if other_peer_id:
                await pubsub.publish(
                    claims.room_id,
                    to=other_peer_id,
                    message=PeerLeftMessage(peer_id=claims.peer_id, reason="left"),
                )
            # A `leave` message doesn't disconnect the socket by itself -- unlike
            # every other way out of the loop above, this is the one path where the
            # connection is still open and we're choosing to end it (docs/protocol.md
            # §3, close code 1000).
            with contextlib.suppress(RuntimeError):
                await _close(websocket, 1000)
            logger.info("ws_left", room_id=claims.room_id, peer_id=claims.peer_id)
        elif is_still_current:
            await presence_repo.set_reconnecting(room_id=claims.room_id, peer_id=claims.peer_id)
            if other_peer_id:
                await pubsub.publish(
                    claims.room_id,
                    to=other_peer_id,
                    message=PeerReconnectingMessage(peer_id=claims.peer_id),
                )
            hub.schedule_reconnect_timeout(
                claims.peer_id,
                grace_seconds=settings.ws_reconnect_grace_seconds,
                on_timeout=_on_reconnect_timeout,
            )
            logger.info("ws_disconnected", room_id=claims.room_id, peer_id=claims.peer_id)
        else:
            logger.info("ws_stale_session_closed", room_id=claims.room_id, peer_id=claims.peer_id)
