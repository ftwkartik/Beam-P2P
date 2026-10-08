"""Cross-instance signaling relay over Redis pub/sub (docs/architecture.md §5,
"Scaling model"; docs/data-model.md's `room:{room_id}` channel).

Every peer-to-peer message is published here, unconditionally, regardless of whether
the addressee is known to be on this instance or some other one -- the whole point is
that a single instance never needs to know where a peer actually lives. Each instance
subscribes to the rooms it has at least one local peer in (reference-counted, since two
local peers of the same room share one subscription) and, for every message that
arrives on a subscribed channel, checks whether the addressee is local and delivers it
through the `SignalingHub` if so. A message addressed to a peer on a *different*
instance is silently ignored by every instance except the one that actually holds them.

**Why polling instead of `pubsub.listen()`:** redis-py's `PubSub.subscribe()` and
`.listen()` are only safe to use from the *same* task -- calling `.subscribe()` from a
different task while another task is already iterating `.listen()` silently loses
messages published afterward (verified against both fakeredis and a real Redis server;
this isn't a fakeredis quirk). Since `subscribe_room`/`unsubscribe_room` are called
from whichever WebSocket connection's own task happens to need them, one dedicated
task instead owns the `PubSub` object exclusively: connection tasks submit subscribe/
unsubscribe requests through a queue, and that owning task alternates between draining
the queue and polling for messages with `get_message(timeout=...)`.

**Why `subscribe_room` waits (with a bounded timeout) for confirmation:**
`asyncio.Queue.put()` on a non-full queue never actually suspends the calling task, so
without waiting for *something*, nothing guarantees the owning task gets scheduled
before the caller goes on to publish a message that depends on the subscription already
being active -- observed directly as lost messages in this exact sequence (connect,
subscribe, immediately publish) when subscribe_room didn't wait for anything. The
timeout is a safety net, not the expected path: draining the queue is normally
near-instant. If it's ever hit, delivery for that room is briefly degraded rather than
the connection hanging forever, and it's logged so the underlying scheduling delay
would actually get noticed.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from dataclasses import dataclass
from typing import Any, Literal

import redis.asyncio as redis
import structlog
from pydantic import BaseModel

from beam_server.services.signaling import SignalingHub

logger = structlog.get_logger(__name__)

#: How often the owning task checks for pending (un)subscribe requests when no
#: message is immediately available. Bounds the extra latency between a peer
#: connecting and this instance actually receiving messages meant for them.
_POLL_INTERVAL_SECONDS = 0.05

#: Safety-net ceiling for subscribe_room/unsubscribe_room's confirmation wait -- see
#: the module docstring. Comfortably above the poll interval, since draining the queue
#: only ever needs the owning task to get one scheduling turn.
_CONFIRMATION_TIMEOUT_SECONDS = 2.0


def _channel(room_id: str) -> str:
    return f"room:{room_id}"


@dataclass(frozen=True, slots=True)
class _SubscriptionRequest:
    action: Literal["subscribe", "unsubscribe"]
    room_id: str
    done: asyncio.Event


class RoomPubSub:
    """One instance's view of the Redis-backed room channels: which rooms it has a
    local stake in, and the background task that owns the pub/sub connection.
    """

    def __init__(self, redis_client: redis.Redis, hub: SignalingHub, instance_id: str) -> None:
        self._redis = redis_client
        self._hub = hub
        self._instance_id = instance_id
        self._pubsub = redis_client.pubsub(ignore_subscribe_messages=True)
        self._refcounts: dict[str, int] = {}
        # Guards read-modify-write of `self._refcounts` and the decision of whether a
        # given subscribe/unsubscribe call is the first-subscriber/last-unsubscriber
        # transition that actually needs to touch Redis. An earlier version mutated
        # the refcount directly in the caller with no lock at all, and an `await`
        # (the `_request` round trip below) sandwiched in that first/last-transition
        # path: two `unsubscribe_room` calls for the same room landing in that gap
        # (observed via a receiver-reload E2E run with a reconnecting peer) could both
        # read the refcount before either wrote it back, drain it to zero between
        # them, and unsubscribe this instance from the room's channel while a
        # different local peer was still on it -- silently cutting off all further
        # signal relay to that peer for the rest of the session. Holding this lock for
        # the whole call (including the `_request` await, when one is needed) makes
        # each transition atomic with respect to every other caller for the same
        # `RoomPubSub`, not just the refcount bookkeeping.
        self._refcount_lock = asyncio.Lock()
        self._pending: asyncio.Queue[_SubscriptionRequest] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """Start the background owning task. Call once, at app startup."""
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        """Stop the owning task and close the pub/sub connection. Call at shutdown."""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        # redis-py's PubSub.aclose() lacks a type annotation in the installed stubs.
        await self._pubsub.aclose()  # type: ignore[no-untyped-call]

    async def subscribe_room(self, room_id: str) -> None:
        """Register this instance's interest in `room_id`'s channel and wait (with a
        bounded timeout -- see the module docstring) for the real subscribe to apply.
        Reference counted: only the first caller for a given room does either -- see
        `self._refcount_lock` for why that counting is safe under concurrent callers.
        """
        async with self._refcount_lock:
            count = self._refcounts.get(room_id, 0) + 1
            self._refcounts[room_id] = count
            if count == 1:
                await self._request("subscribe", room_id)

    async def unsubscribe_room(self, room_id: str) -> None:
        """Release this instance's interest in `room_id`. The underlying Redis
        UNSUBSCRIBE only happens once every local peer of that room is gone -- see
        `self._refcount_lock` for why that counting is safe under concurrent callers.
        """
        async with self._refcount_lock:
            count = self._refcounts.get(room_id, 0) - 1
            if count <= 0:
                self._refcounts.pop(room_id, None)
                if count == 0:
                    await self._request("unsubscribe", room_id)
            else:
                self._refcounts[room_id] = count

    async def _request(self, action: Literal["subscribe", "unsubscribe"], room_id: str) -> None:
        done = asyncio.Event()
        await self._pending.put(_SubscriptionRequest(action=action, room_id=room_id, done=done))
        try:
            await asyncio.wait_for(done.wait(), timeout=_CONFIRMATION_TIMEOUT_SECONDS)
        except TimeoutError:
            logger.warning("pubsub_confirmation_timed_out", action=action, room_id=room_id)

    async def publish(self, room_id: str, *, to: str, message: BaseModel) -> None:
        """Publish `message` to `room_id`'s channel, addressed to peer `to`.

        Delivery is not guaranteed (Redis pub/sub is fire-and-forget: a message
        published while a subscriber is briefly disconnected is simply lost). The
        signaling protocol tolerates this -- WebRTC negotiation retries and ICE
        restarts cover a dropped signal the same way they'd cover any other transient
        network hiccup (docs/architecture.md §5).
        """
        envelope = {
            "to": to,
            "msg": message.model_dump_json(by_alias=True),
            "origin_instance": self._instance_id,
            "ts": time.time(),
        }
        await self._redis.publish(_channel(room_id), json.dumps(envelope))

    async def _run(self) -> None:
        """The one task allowed to touch `self._pubsub`: alternates between applying
        pending subscribe/unsubscribe requests and polling for messages.
        """
        try:
            while True:
                await self._drain_pending()

                if not self._pubsub.subscribed:
                    # get_message() raises if there's no connection yet, which is the
                    # case whenever this instance currently has zero rooms subscribed
                    # (e.g. right at startup, or between all peers disconnecting and
                    # the next one connecting) -- an ordinary state, not an error.
                    await asyncio.sleep(_POLL_INTERVAL_SECONDS)
                    continue

                try:
                    raw = await self._pubsub.get_message(timeout=_POLL_INTERVAL_SECONDS)
                except Exception:
                    logger.exception("pubsub_get_message_failed")
                    await asyncio.sleep(_POLL_INTERVAL_SECONDS)
                    continue
                if raw is not None:
                    await self._dispatch(raw)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("pubsub_run_loop_crashed")
            raise

    async def _drain_pending(self) -> None:
        while not self._pending.empty():
            request = self._pending.get_nowait()
            channel = _channel(request.room_id)
            if request.action == "subscribe":
                await self._pubsub.subscribe(channel)
            else:
                await self._pubsub.unsubscribe(channel)
            request.done.set()

    async def _dispatch(self, raw: dict[str, Any]) -> None:
        if raw.get("type") != "message":
            return

        try:
            envelope = json.loads(raw["data"])
        except (TypeError, ValueError):
            logger.warning("pubsub_envelope_undecodable", raw_type=type(raw.get("data")).__name__)
            return

        to = envelope.get("to")
        text = envelope.get("msg")
        if not isinstance(to, str) or not isinstance(text, str):
            logger.warning("pubsub_envelope_malformed")
            return

        # Most deliveries on a shared room channel aren't for us -- every instance
        # with a local peer in this room is subscribed, but a given message is only
        # ever addressed to one of them.
        await self._hub.send_text(to, text)
