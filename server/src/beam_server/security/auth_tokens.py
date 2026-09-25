"""Access and refresh tokens for accounts (Milestone 11; docs/adr/010).

Two different token shapes, deliberately: the **access token** is a short-lived JWT
(same HS256-pinned-on-decode pattern as security/tokens.py's room tokens, just a
different audience so one can never be replayed as the other) that proves who's
signed in without a DB round trip on every request. The **refresh token** is an
opaque random string, DB-backed and rotated on every use -- a JWT refresh token would
be revocable only by waiting out its own expiry, which defeats the point of a
refresh token (the thing you call to recover from a *stolen* token).

Rotation: each successful `/auth/refresh` call revokes the presented token and issues
a brand new one. A revoked token presented again is reuse -- of a stolen or replayed
token, since the legitimate client would hold the newer one instead -- and is
rejected. This is the simple single-token version of the rotation docs/data-model.md
sketches, not the full reuse-detection-with-token-families scheme (which also revokes
every other token issued to that session on reuse): that's real additional protection
worth adding later, but a single compromised-token detection is the part that actually
matters for an MVP, and the family bookkeeping isn't.
"""

from __future__ import annotations

import hashlib
import secrets
import time
import uuid
from dataclasses import dataclass

import jwt

ALGORITHM = "HS256"
#: Scopes an access token to this system so it can never be replayed as a room token
#: (security/tokens.py) or vice versa, even though both are HS256 JWTs signed with
#: the same JWT_SECRET.
AUDIENCE = "beam-accounts"
#: Random refresh tokens are 256 bits of entropy -- plenty, and the DB stores only
#: their SHA-256 (a database leak alone shouldn't hand out live sessions), which is a
#: fast deterministic hash deliberately: this is a high-entropy random token, not a
#: low-entropy human password, so there's no offline-guessing risk slow hashing (like
#: the argon2id in passwords.py) exists to defend against.
REFRESH_TOKEN_BYTES = 32


class TokenError(ValueError):
    """Raised when a token is missing, malformed, expired, or fails validation."""


@dataclass(frozen=True, slots=True)
class AccessTokenClaims:
    user_id: str
    jti: str
    issued_at: int
    expires_at: int


def create_access_token(*, secret: str, user_id: str, ttl_seconds: int) -> str:
    now = int(time.time())
    payload = {
        "sub": user_id,
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + ttl_seconds,
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def decode_access_token(*, secret: str, token: str) -> AccessTokenClaims:
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[ALGORITHM],
            audience=AUDIENCE,
            options={"require": ["sub", "aud", "iat", "exp", "jti"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenError(f"invalid access token: {exc}") from exc
    return AccessTokenClaims(
        user_id=payload["sub"],
        jti=payload["jti"],
        issued_at=payload["iat"],
        expires_at=payload["exp"],
    )


def generate_refresh_token() -> str:
    return secrets.token_urlsafe(REFRESH_TOKEN_BYTES)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
