"""In-process signaling relay: who's connected to this instance right now, and
delivering messages to them (docs/architecture.md §4, "Key flows").

This is intentionally scoped to *this process* -- it has no idea whether a given peer
ID belongs to some other instance at all. `store.pubsub.RoomPubSub` is the piece that
makes cross-instance delivery work (Milestone 5): every peer-to-peer message is
published to the room's Redis channel regardless of where the addressee actually is,
and each subscribed instance's pub/sub listener calls back into its own hub via
`send_text` only when the addressee turns out to be local.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import structlog
from pydantic import BaseModel

from beam_server.ws.session import PeerSession

logger = structlog.get_logger(__name__)


class SignalingHub:
    """Tracks live sessions and pending reconnect-grace timers for this process."""

    def __init__(self) -> None:
        self._sessions: dict[str, PeerSession] = {}
        self._reconnect_tasks: dict[str, asyncio.Task[None]] = {}

    def get(self, peer_id: str) -> PeerSession | None:
        return self._sessions.get(peer_id)

    def register(self, session: PeerSession) -> PeerSession | None:
        """Register a session, returning whichever session previously occupied that
        peer ID, if any (the caller must close it -- see docs/protocol.md's 4409).
        """
        previous = self._sessions.get(session.peer_id)
        self._sessions[session.peer_id] = session
        return previous

    def unregister(self, peer_id: str, *, only_if: PeerSession | None = None) -> None:
        """Remove a session. If `only_if` is given, only removes it when it's still
        the current session for that peer -- so a stale cleanup can't evict a newer
        session that has already replaced it.
        """
        current = self._sessions.get(peer_id)
        if current is None:
            return
        if only_if is not None and current is not only_if:
            return
        del self._sessions[peer_id]

    async def send(self, peer_id: str, message: BaseModel) -> bool:
        """Serialize `message` and deliver it to a locally connected peer."""
        return await self.send_text(peer_id, message.model_dump_json(by_alias=True))

    async def send_text(self, peer_id: str, text: str) -> bool:
        """Best-effort delivery of a raw, already-serialized message to a locally
        connected peer. This is what `store.pubsub.RoomPubSub` calls after receiving a
        message over Redis addressed to a peer that turns out to live on this
        instance -- the payload has already been serialized once by whichever instance
        published it, so there's no need to decode and re-encode it here.

        Returns whether it was (as far as we can tell) actually sent -- the peer not
        being connected here, or the socket having just died, are both ordinary and
        not logged as errors.
        """
        session = self._sessions.get(peer_id)
        if session is None:
            return False
        try:
            await session.websocket.send_text(text)
        except (RuntimeError, ConnectionError) as exc:
            logger.debug("signaling_send_failed", peer_id=peer_id, error=str(exc))
            return False
        return True

    def is_reconnecting(self, peer_id: str) -> bool:
        """True if `peer_id` has an active reconnect-grace timer pending."""
        return peer_id in self._reconnect_tasks

    def cancel_reconnect_timer(self, peer_id: str) -> bool:
        """Cancel a pending reconnect-grace timer, if any. Returns whether one existed."""
        task = self._reconnect_tasks.pop(peer_id, None)
        if task is None:
            return False
        if not task.done():
            task.cancel()
        return True

    def schedule_reconnect_timeout(
        self,
        peer_id: str,
        *,
        grace_seconds: float,
        on_timeout: Callable[[], Awaitable[None]],
    ) -> None:
        """Run `on_timeout` after `grace_seconds`, unless cancelled first (the peer
        reconnected) -- docs/protocol.md §3's reconnect grace period.
        """
        self.cancel_reconnect_timer(peer_id)

        async def _wait() -> None:
            try:
                await asyncio.sleep(grace_seconds)
            except asyncio.CancelledError:
                return
            self._reconnect_tasks.pop(peer_id, None)
            await on_timeout()

        self._reconnect_tasks[peer_id] = asyncio.create_task(_wait())
