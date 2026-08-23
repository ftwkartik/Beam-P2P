"""Per-peer presence in Redis (docs/data-model.md §1, `room:{room_id}:peer:{peer_id}`).

This is deliberately separate from the in-process peer registry
(`services.signaling.SignalingHub`), which holds the actual live WebSocket objects and
can only ever describe *this* process. Presence describes "is this peer online, and if
so on which instance", which is what lets Milestone 5's multi-instance fan-out find the
right process to deliver a message through. In the single-instance Milestone 4 setup,
presence is written for correctness and observability even though relay itself still
goes through the local, in-process registry.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal, cast

import redis.asyncio as redis

PresenceState = Literal["online", "reconnecting"]

#: Refreshed well inside this so a live peer's presence never lapses between
#: heartbeats; a crashed process's presence still disappears within one TTL window.
_PRESENCE_TTL_SECONDS = 60


def _presence_key(room_id: str, peer_id: str) -> str:
    return f"room:{room_id}:peer:{peer_id}"


@dataclass(frozen=True, slots=True)
class PresenceRecord:
    role: str
    state: PresenceState
    instance_id: str
    last_seen: float


class PresenceRepository:
    def __init__(self, redis_client: redis.Redis) -> None:
        self._redis = redis_client

    async def set_online(self, *, room_id: str, peer_id: str, role: str, instance_id: str) -> None:
        key = _presence_key(room_id, peer_id)
        mapping = {
            "role": role,
            "state": "online",
            "instance_id": instance_id,
            "last_seen": str(time.time()),
        }
        pipe = self._redis.pipeline()
        pipe.hset(key, mapping=mapping)  # type: ignore[arg-type]
        pipe.expire(key, _PRESENCE_TTL_SECONDS)
        await pipe.execute()

    async def set_reconnecting(self, *, room_id: str, peer_id: str) -> None:
        key = _presence_key(room_id, peer_id)
        await self._redis.hset(key, "state", "reconnecting")

    async def refresh(self, *, room_id: str, peer_id: str) -> None:
        """Heartbeat: extend the TTL and bump `last_seen` without changing state."""
        key = _presence_key(room_id, peer_id)
        pipe = self._redis.pipeline()
        pipe.hset(key, "last_seen", str(time.time()))
        pipe.expire(key, _PRESENCE_TTL_SECONDS)
        await pipe.execute()

    async def get(self, *, room_id: str, peer_id: str) -> PresenceRecord | None:
        data = cast("dict[str, str]", await self._redis.hgetall(_presence_key(room_id, peer_id)))
        if not data:
            return None
        return PresenceRecord(
            role=data["role"],
            state=data["state"],  # type: ignore[arg-type]
            instance_id=data["instance_id"],
            last_seen=float(data["last_seen"]),
        )

    async def delete(self, *, room_id: str, peer_id: str) -> None:
        await self._redis.delete(_presence_key(room_id, peer_id))
