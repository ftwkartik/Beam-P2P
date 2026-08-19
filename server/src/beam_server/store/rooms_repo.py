"""Redis-backed room storage (docs/data-model.md §1, "Redis keyspace").

All room state is ephemeral and TTL'd -- there is no `create_all()`-style schema to
migrate, and nothing here is a source of truth beyond its own TTL. State transitions
that must not race (recording a failed attempt, accepting a join) are Lua scripts, so
two concurrent requests can't both succeed at something only one of them should
(docs/adr/002-redis-state-and-pubsub.md).
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import Literal, cast

import redis.asyncio as redis

RoomState = Literal["waiting", "paired", "burned"]

#: Nameplates are tried in this range first; a `NameplateSpaceExhausted` widening
#: pushes into the full range up to `codes.MAX_NAMEPLATE` (docs/data-model.md).
_SMALL_NAMEPLATE_RANGE_END = 999
_ALLOCATION_ATTEMPTS_PER_RANGE = 30


def _room_key(room_id: str) -> str:
    return f"room:{room_id}"


def _nameplate_key(nameplate: int) -> str:
    return f"nameplate:{nameplate}"


def _attempts_key(room_id: str) -> str:
    return f"room:{room_id}:attempts"


class NameplateSpaceExhausted(RuntimeError):
    """Raised if no free nameplate could be allocated after exhausting both ranges."""


@dataclass(frozen=True, slots=True)
class RoomRecord:
    room_id: str
    nameplate: int
    code_hmac: str
    state: RoomState
    created_at: float
    expires_at: float
    creator_peer: str
    joiner_peer: str | None

    @classmethod
    def from_redis_hash(cls, room_id: str, data: dict[str, str]) -> RoomRecord:
        return cls(
            room_id=room_id,
            nameplate=int(data["nameplate"]),
            code_hmac=data["code_hmac"],
            state=data["state"],  # type: ignore[arg-type]
            created_at=float(data["created_at"]),
            expires_at=float(data["expires_at"]),
            creator_peer=data["creator_peer"],
            joiner_peer=data.get("joiner_peer") or None,
        )


# Atomically increments the failed-attempt counter and, on reaching the limit, marks
# the room burned. Returns {attempts, burned (0/1)}.
_RECORD_FAILED_ATTEMPT_SCRIPT = """
local attempts_key = KEYS[1]
local room_key = KEYS[2]
local max_attempts = tonumber(ARGV[1])
local ttl_seconds = tonumber(ARGV[2])

local attempts = redis.call('INCR', attempts_key)
if redis.call('TTL', attempts_key) < 0 then
    redis.call('EXPIRE', attempts_key, ttl_seconds)
end

local burned = 0
if attempts >= max_attempts then
    if redis.call('EXISTS', room_key) == 1 then
        redis.call('HSET', room_key, 'state', 'burned')
    end
    burned = 1
end

return {attempts, burned}
"""

# Atomically transitions a room from "waiting" to "paired" if (and only if) it is
# still "waiting". Returns the room's state *before* this call: "waiting" means this
# call won the race and performed the transition; any other value means it didn't.
_TRY_JOIN_SCRIPT = """
local room_key = KEYS[1]
local joiner_peer = ARGV[1]

local state = redis.call('HGET', room_key, 'state')
if state == false then
    return 'not_found'
end
if state == 'waiting' then
    redis.call('HSET', room_key, 'state', 'paired', 'joiner_peer', joiner_peer)
end
return state
"""


class RoomsRepository:
    """Redis operations for room lifecycle. One instance per request is cheap; the
    Lua scripts are registered lazily and cached by the underlying redis-py client.
    """

    def __init__(self, redis_client: redis.Redis) -> None:
        self._redis = redis_client
        self._record_failed_attempt = redis_client.register_script(_RECORD_FAILED_ATTEMPT_SCRIPT)
        self._try_join = redis_client.register_script(_TRY_JOIN_SCRIPT)

    async def allocate_nameplate(self, room_id: str, ttl_seconds: int) -> int:
        """Reserve a free nameplate pointing at `room_id`.

        Tries the small range first (cheap to guess-avoid collisions on, since it's
        mostly empty in normal operation) and widens to the full range under
        contention (docs/data-model.md, "Nameplate allocation").
        """
        for low, high in ((1, _SMALL_NAMEPLATE_RANGE_END), (_SMALL_NAMEPLATE_RANGE_END + 1, 9999)):
            for _ in range(_ALLOCATION_ATTEMPTS_PER_RANGE):
                candidate = secrets.randbelow(high - low + 1) + low
                acquired = await self._redis.set(
                    _nameplate_key(candidate), room_id, nx=True, ex=ttl_seconds
                )
                if acquired:
                    return candidate
        raise NameplateSpaceExhausted("could not allocate a nameplate; all ranges exhausted")

    async def create_room(
        self,
        *,
        room_id: str,
        nameplate: int,
        code_hmac: str,
        creator_peer: str,
        ttl_seconds: int,
    ) -> RoomRecord:
        now = time.time()
        expires_at = now + ttl_seconds
        mapping = {
            "nameplate": str(nameplate),
            "code_hmac": code_hmac,
            "state": "waiting",
            "created_at": str(now),
            "expires_at": str(expires_at),
            "creator_peer": creator_peer,
        }
        pipe = self._redis.pipeline()
        # redis-py's stubs for `mapping` don't line up with a plain dict[str, str]
        # even though that's exactly what's valid at runtime with decode_responses=True.
        pipe.hset(_room_key(room_id), mapping=mapping)  # type: ignore[arg-type]
        pipe.expire(_room_key(room_id), ttl_seconds)
        await pipe.execute()
        return RoomRecord(
            room_id=room_id,
            nameplate=nameplate,
            code_hmac=code_hmac,
            state="waiting",
            created_at=now,
            expires_at=expires_at,
            creator_peer=creator_peer,
            joiner_peer=None,
        )

    async def get_room_id_by_nameplate(self, nameplate: int) -> str | None:
        # Our client is constructed with decode_responses=True, so this is always a
        # str at runtime even though the stubs allow bytes.
        return cast("str | None", await self._redis.get(_nameplate_key(nameplate)))

    async def get_room(self, room_id: str) -> RoomRecord | None:
        data = cast("dict[str, str]", await self._redis.hgetall(_room_key(room_id)))
        if not data:
            return None
        return RoomRecord.from_redis_hash(room_id, data)

    async def record_failed_attempt(
        self, room_id: str, *, max_attempts: int, ttl_seconds: int
    ) -> tuple[int, bool]:
        """Returns (attempts_so_far, just_burned)."""
        attempts, burned = await self._record_failed_attempt(
            keys=[_attempts_key(room_id), _room_key(room_id)],
            args=[max_attempts, ttl_seconds],
        )
        return int(attempts), bool(int(burned))

    async def try_join(self, room_id: str, joiner_peer: str) -> RoomState | Literal["not_found"]:
        """Attempt the waiting->paired transition. See `_TRY_JOIN_SCRIPT` for the
        exact semantics of the returned value.
        """
        result: str = await self._try_join(keys=[_room_key(room_id)], args=[joiner_peer])
        return result  # type: ignore[return-value]

    async def delete_room(self, room_id: str, nameplate: int) -> None:
        pipe = self._redis.pipeline()
        pipe.delete(_room_key(room_id))
        pipe.delete(_nameplate_key(nameplate))
        pipe.delete(_attempts_key(room_id))
        await pipe.execute()
