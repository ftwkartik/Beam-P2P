"""Signaling WebSocket client (docs/protocol.md §3). Direct counterpart of the web
client's `engine/signaling.ts`, built on the `websockets` library.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import websockets
from pydantic import TypeAdapter

from beam_cli import __version__
from beam_protocol.signaling import (
    ClientInfo,
    ClientMessage,
    ClientSignalMessage,
    HelloMessage,
    LeaveMessage,
    PeerJoinedMessage,
    PeerLeftMessage,
    PeerReconnectingMessage,
    ServerErrorMessage,
    ServerMessage,
    ServerSignalMessage,
    SignalData,
    WelcomeMessage,
)

#: Close codes the server sends that mean "don't bother reconnecting" (docs/protocol.md
#: §3, "Close codes") -- mirrors signaling.ts's fatal-code list exactly.
FATAL_CLOSE_CODES = frozenset({4400, 4401, 4403, 4404, 4408, 4409, 4413, 4429})

_server_message_adapter: TypeAdapter[ServerMessage] = TypeAdapter(ServerMessage)


class SignalingError(Exception):
    """The connection closed for good (a fatal close code, or retries exhausted)."""


@dataclass
class SignalingCallbacks:
    on_welcome: Callable[[WelcomeMessage], None] | None = None
    on_peer_joined: Callable[[PeerJoinedMessage], None] | None = None
    on_peer_reconnecting: Callable[[PeerReconnectingMessage], None] | None = None
    on_peer_left: Callable[[PeerLeftMessage], None] | None = None
    on_signal: Callable[[str, SignalData], Awaitable[None] | None] | None = None
    on_error: Callable[[ServerErrorMessage], None] | None = None
    #: Called once the connection is unrecoverable; never called for a clean `leave()`.
    on_fatal: Callable[[str], None] | None = None


@dataclass
class SignalingClient:
    """One instance per room connection. `token` is a room token (docs/security's
    "room- and peer-bound JWT"); reconnecting later with the same token (after a drop,
    or a fresh process holding a persisted token -- see resume.py) is what makes
    resume possible without the server treating it as a brand-new peer.
    """

    ws_url: str
    token: str
    callbacks: SignalingCallbacks = field(default_factory=SignalingCallbacks)
    client_version: str = __version__

    _ws: websockets.ClientConnection | None = field(default=None, init=False, repr=False)
    _recv_task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    _closing: bool = field(default=False, init=False, repr=False)

    async def connect(self) -> None:
        """Opens the connection and sends `hello`. Raises `SignalingError` if the
        server closes before a `welcome` ever arrives (e.g. an invalid token)."""
        self._ws = await websockets.connect(self.ws_url)
        await self._send(
            HelloMessage(
                token=self.token,
                client=ClientInfo(kind="cli", version=self.client_version),
            )
        )
        self._recv_task = asyncio.create_task(self._recv_loop())

    async def send_signal(self, data: SignalData) -> None:
        await self._send(ClientSignalMessage(data=data))

    async def leave(self) -> None:
        """A graceful departure (docs/protocol.md §3): the server tells the peer
        immediately, with no reconnect-grace wait."""
        self._closing = True
        if self._ws is not None:
            with contextlib.suppress(websockets.ConnectionClosed):
                await self._send(LeaveMessage())
        await self.close()

    async def close(self) -> None:
        self._closing = True
        if self._recv_task is not None:
            self._recv_task.cancel()
        if self._ws is not None:
            await self._ws.close()

    async def wait_closed(self) -> None:
        if self._recv_task is not None:
            await asyncio.gather(self._recv_task, return_exceptions=True)

    async def _send(self, message: ClientMessage) -> None:
        if self._ws is None:
            raise SignalingError("not connected")
        await self._ws.send(message.model_dump_json(by_alias=True))

    async def _recv_loop(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                await self._dispatch(_server_message_adapter.validate_json(raw))
        except websockets.ConnectionClosed as exc:
            if self._closing:
                return
            code = exc.rcvd.code if exc.rcvd else None
            if code in FATAL_CLOSE_CODES:
                reason = exc.rcvd.reason if exc.rcvd else ""
                if self.callbacks.on_fatal:
                    detail = reason or "unknown reason"
                    self.callbacks.on_fatal(f"connection closed ({code}): {detail}")
            elif self.callbacks.on_fatal:
                # A non-fatal close (network drop, server restart): this class does
                # not reconnect on its own -- see resume.py / session.py, which own
                # deciding whether and how to try again with a fresh SignalingClient.
                self.callbacks.on_fatal(f"connection lost ({code})")

    async def _dispatch(self, message: ServerMessage) -> None:
        callbacks = self.callbacks
        if isinstance(message, WelcomeMessage):
            if callbacks.on_welcome:
                callbacks.on_welcome(message)
        elif isinstance(message, PeerJoinedMessage):
            if callbacks.on_peer_joined:
                callbacks.on_peer_joined(message)
        elif isinstance(message, PeerReconnectingMessage):
            if callbacks.on_peer_reconnecting:
                callbacks.on_peer_reconnecting(message)
        elif isinstance(message, PeerLeftMessage):
            if callbacks.on_peer_left:
                callbacks.on_peer_left(message)
        elif isinstance(message, ServerSignalMessage):
            if callbacks.on_signal:
                result = callbacks.on_signal(message.from_, message.data)
                if result is not None:
                    await result
        elif isinstance(message, ServerErrorMessage) and callbacks.on_error:
            callbacks.on_error(message)
