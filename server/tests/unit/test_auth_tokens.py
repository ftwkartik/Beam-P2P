"""Access/refresh token creation and validation (Milestone 11; docs/adr/010)."""

from __future__ import annotations

import time

import jwt
import pytest

from beam_server.security.auth_tokens import (
    ALGORITHM,
    AUDIENCE,
    TokenError,
    create_access_token,
    decode_access_token,
    generate_refresh_token,
    hash_refresh_token,
)

SECRET = "a" * 32


def test_create_and_decode_round_trip() -> None:
    token = create_access_token(secret=SECRET, user_id="user-1", ttl_seconds=3600)
    claims = decode_access_token(secret=SECRET, token=token)
    assert claims.user_id == "user-1"
    assert claims.jti


def test_two_tokens_have_different_jti() -> None:
    a = decode_access_token(
        secret=SECRET, token=create_access_token(secret=SECRET, user_id="u", ttl_seconds=60)
    )
    b = decode_access_token(
        secret=SECRET, token=create_access_token(secret=SECRET, user_id="u", ttl_seconds=60)
    )
    assert a.jti != b.jti


def test_expired_token_is_rejected() -> None:
    token = create_access_token(secret=SECRET, user_id="u", ttl_seconds=-1)
    with pytest.raises(TokenError):
        decode_access_token(secret=SECRET, token=token)


def test_wrong_secret_is_rejected() -> None:
    token = create_access_token(secret=SECRET, user_id="u", ttl_seconds=60)
    with pytest.raises(TokenError):
        decode_access_token(secret="b" * 32, token=token)


def test_room_token_cannot_be_replayed_as_an_access_token() -> None:
    """A room token (security/tokens.py) is also an HS256 JWT signed with the same
    JWT_SECRET -- the different `aud` claim is what stops one being accepted as the
    other, not the secret (docs/security.md's "JWT with aud ... peer-bound")."""
    now = int(time.time())
    room_shaped_token = jwt.encode(
        {
            "sub": "peer-1",
            "room": "room-1",
            "role": "creator",
            "aud": "beam-signaling",
            "iat": now,
            "exp": now + 60,
            "jti": "x",
        },
        SECRET,
        algorithm=ALGORITHM,
    )
    with pytest.raises(TokenError):
        decode_access_token(secret=SECRET, token=room_shaped_token)


def test_access_token_audience_is_accounts_specific() -> None:
    token = create_access_token(secret=SECRET, user_id="u", ttl_seconds=60)
    payload = jwt.decode(token, SECRET, algorithms=[ALGORITHM], audience=AUDIENCE)
    assert payload["aud"] == "beam-accounts"


def test_refresh_token_is_high_entropy_and_url_safe() -> None:
    token = generate_refresh_token()
    assert len(token) > 32
    assert all(c.isalnum() or c in "-_" for c in token)


def test_two_refresh_tokens_differ() -> None:
    assert generate_refresh_token() != generate_refresh_token()


def test_refresh_token_hash_is_deterministic_and_not_the_token_itself() -> None:
    token = generate_refresh_token()
    h1 = hash_refresh_token(token)
    h2 = hash_refresh_token(token)
    assert h1 == h2
    assert h1 != token
