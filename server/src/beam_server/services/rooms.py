"""Room lifecycle: create, join, authorize, close (docs/architecture.md §6).

This is where the pieces from `store.rooms_repo`, `security.tokens` and
`beam_protocol.codes` meet. Routers (api/rooms.py) call this service; they never touch
Redis or the Lua scripts directly.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass

import redis.asyncio as redis

from beam_protocol.codes import (
    InvalidCodeError as CodeFormatError,
)
from beam_protocol.codes import (
    format_code,
    generate_words,
    normalize_code,
)
from beam_server.errors import (
    InvalidRoomCodeError,
    NotFoundError,
    RateLimitedError,
    RoomBurnedError,
    RoomFullError,
    UnauthorizedError,
)
from beam_server.security.tokens import (
    RoomTokenClaims,
    TokenError,
    create_room_token,
    decode_room_token,
)
from beam_server.store.rate_limit import RateLimitExceeded, check_rate_limit
from beam_server.store.rooms_repo import RoomRecord, RoomsRepository

#: Length, in bytes, of a generated peer ID before urlsafe-base64 encoding.
_PEER_ID_ENTROPY_BYTES = 16


def _generate_peer_id() -> str:
    return secrets.token_urlsafe(_PEER_ID_ENTROPY_BYTES)


def _compute_code_hmac(pepper: str, room_id: str, words: tuple[str, ...]) -> str:
    message = f"{room_id}:{'-'.join(words)}".encode()
    return hmac.new(pepper.encode(), message, hashlib.sha256).hexdigest()


@dataclass(frozen=True, slots=True)
class CreatedRoom:
    room_id: str
    code: str
    nameplate: int
    expires_at: float
    token: str
    peer_id: str


@dataclass(frozen=True, slots=True)
class JoinedRoom:
    room_id: str
    expires_at: float
    token: str
    peer_id: str


class RoomService:
    def __init__(
        self,
        *,
        repo: RoomsRepository,
        redis_client: redis.Redis,
        jwt_secret: str,
        code_pepper: str,
        waiting_ttl_seconds: int,
        max_join_attempts: int,
        token_ttl_seconds: int,
        join_per_nameplate_limit: int,
        join_per_nameplate_window_seconds: int = 60,
    ) -> None:
        self._repo = repo
        self._redis = redis_client
        self._jwt_secret = jwt_secret
        self._code_pepper = code_pepper
        self._waiting_ttl_seconds = waiting_ttl_seconds
        self._max_join_attempts = max_join_attempts
        self._token_ttl_seconds = token_ttl_seconds
        self._join_per_nameplate_limit = join_per_nameplate_limit
        self._join_per_nameplate_window_seconds = join_per_nameplate_window_seconds

    async def create_room(self) -> CreatedRoom:
        room_id = str(uuid.uuid4())
        nameplate = await self._repo.allocate_nameplate(room_id, self._waiting_ttl_seconds)
        words = generate_words()
        code_hmac = _compute_code_hmac(self._code_pepper, room_id, words)
        creator_peer = _generate_peer_id()

        room = await self._repo.create_room(
            room_id=room_id,
            nameplate=nameplate,
            code_hmac=code_hmac,
            creator_peer=creator_peer,
            ttl_seconds=self._waiting_ttl_seconds,
        )

        token = create_room_token(
            secret=self._jwt_secret,
            peer_id=creator_peer,
            room_id=room_id,
            role="creator",
            ttl_seconds=self._token_ttl_seconds,
        )

        return CreatedRoom(
            room_id=room_id,
            code=format_code(nameplate, words),
            nameplate=nameplate,
            expires_at=room.expires_at,
            token=token,
            peer_id=creator_peer,
        )

    async def join_room(self, raw_code: str) -> JoinedRoom:
        try:
            parsed = normalize_code(raw_code)
        except CodeFormatError as exc:
            raise InvalidRoomCodeError("This room code isn't valid.") from exc

        # Throttle per-nameplate before touching room state, so hammering one
        # nameplate with garbage words is bounded independently of the caller's IP
        # (docs/data-model.md's `rl:join_nameplate:*` scope).
        try:
            await check_rate_limit(
                self._redis,
                scope="join_nameplate",
                key=str(parsed.nameplate),
                limit=self._join_per_nameplate_limit,
                window_seconds=self._join_per_nameplate_window_seconds,
            )
        except RateLimitExceeded as exc:
            raise RateLimitedError(
                "Too many attempts against this room. Please slow down.",
                retry_after=exc.retry_after,
            ) from exc

        room_id = await self._repo.get_room_id_by_nameplate(parsed.nameplate)
        if room_id is None:
            raise InvalidRoomCodeError("This room code isn't valid.")

        room = await self._repo.get_room(room_id)
        if room is None:
            raise InvalidRoomCodeError("This room code isn't valid.")

        if room.state == "burned":
            raise RoomBurnedError("This room was closed after too many wrong attempts.")
        if room.state == "paired":
            raise RoomFullError("This room already has two peers.")

        expected_hmac = _compute_code_hmac(self._code_pepper, room_id, parsed.words)
        if not hmac.compare_digest(expected_hmac, room.code_hmac):
            await self._repo.record_failed_attempt(
                room_id,
                max_attempts=self._max_join_attempts,
                ttl_seconds=self._waiting_ttl_seconds,
            )
            # Deliberately the same error regardless of whether this attempt just
            # burned the room: burning only changes what *future* attempts see.
            raise InvalidRoomCodeError("This room code isn't valid.")

        joiner_peer = _generate_peer_id()
        previous_state = await self._repo.try_join(room_id, joiner_peer)
        if previous_state == "paired":
            raise RoomFullError("This room already has two peers.")
        if previous_state == "burned":
            raise RoomBurnedError("This room was closed after too many wrong attempts.")
        if previous_state == "not_found":
            raise InvalidRoomCodeError("This room code isn't valid.")
        # previous_state == "waiting": we won the race and are now the joiner.

        token = create_room_token(
            secret=self._jwt_secret,
            peer_id=joiner_peer,
            room_id=room_id,
            role="joiner",
            ttl_seconds=self._token_ttl_seconds,
        )

        return JoinedRoom(
            room_id=room_id,
            expires_at=room.expires_at,
            token=token,
            peer_id=joiner_peer,
        )

    def authorize(self, token: str, *, room_id: str) -> RoomTokenClaims:
        """Validate a room token and confirm it's scoped to `room_id`."""
        try:
            claims = decode_room_token(secret=self._jwt_secret, token=token)
        except TokenError as exc:
            raise UnauthorizedError("Invalid or expired room token.") from exc

        if claims.room_id != room_id:
            raise UnauthorizedError("This token isn't valid for this room.")

        return claims

    async def get_room_or_404(self, room_id: str) -> RoomRecord:
        room = await self._repo.get_room(room_id)
        if room is None:
            raise NotFoundError("Room could not be found.")
        return room

    async def close_room(self, room_id: str, claims: RoomTokenClaims) -> None:
        """Only the creator may close a room early (docs/protocol.md §2)."""
        if claims.role != "creator":
            raise UnauthorizedError("Only the room's creator can close it.")
        room = await self.get_room_or_404(room_id)
        await self._repo.delete_room(room_id, room.nameplate)
