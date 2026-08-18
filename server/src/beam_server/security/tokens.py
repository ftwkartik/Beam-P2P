"""Room tokens: short-lived, room- and peer-bound JWTs (docs/protocol.md §2, "Room
token"; docs/adr/003-room-codes-and-tokens.md).

A token authorizes exactly one peer for exactly one room. It is presented as a Bearer
token to `/rooms/{id}/ice-servers` and `DELETE /rooms/{id}`, and (from Milestone 4) in
the WebSocket `hello` message. The algorithm is pinned on decode -- accepting whatever
`alg` the token claims would let an attacker choose `none` or a weaker algorithm.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Literal

import jwt

Role = Literal["creator", "joiner"]

#: Only algorithm ever accepted, regardless of what a token's header claims.
ALGORITHM = "HS256"
#: Every room token is scoped to this audience so it can never be replayed against a
#: different kind of JWT the system might issue in the future (e.g. a user session).
AUDIENCE = "beam-signaling"


class TokenError(ValueError):
    """Raised when a token is missing, malformed, expired, or fails validation."""


@dataclass(frozen=True, slots=True)
class RoomTokenClaims:
    peer_id: str
    room_id: str
    role: Role
    jti: str
    issued_at: int
    expires_at: int


def create_room_token(
    *,
    secret: str,
    peer_id: str,
    room_id: str,
    role: Role,
    ttl_seconds: int,
) -> str:
    """Issue a room token for `peer_id` in `room_id`, valid for `ttl_seconds`."""
    now = int(time.time())
    payload = {
        "sub": peer_id,
        "room": room_id,
        "role": role,
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + ttl_seconds,
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def decode_room_token(*, secret: str, token: str) -> RoomTokenClaims:
    """Validate and decode a room token. Raises `TokenError` on any problem."""
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[ALGORITHM],
            audience=AUDIENCE,
            options={"require": ["sub", "room", "role", "aud", "iat", "exp", "jti"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenError(f"invalid room token: {exc}") from exc

    role = payload["role"]
    if role not in ("creator", "joiner"):
        raise TokenError(f"invalid role claim: {role!r}")

    return RoomTokenClaims(
        peer_id=payload["sub"],
        room_id=payload["room"],
        role=role,
        jti=payload["jti"],
        issued_at=payload["iat"],
        expires_at=payload["exp"],
    )
