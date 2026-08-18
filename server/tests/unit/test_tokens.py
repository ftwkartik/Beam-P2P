"""Room token creation and validation (docs/adr/003-room-codes-and-tokens.md)."""

from __future__ import annotations

import time

import jwt
import pytest

from beam_server.security.tokens import (
    ALGORITHM,
    AUDIENCE,
    TokenError,
    create_room_token,
    decode_room_token,
)

SECRET = "a" * 32


def test_create_and_decode_round_trip() -> None:
    token = create_room_token(
        secret=SECRET, peer_id="peer-1", room_id="room-1", role="creator", ttl_seconds=3600
    )
    claims = decode_room_token(secret=SECRET, token=token)
    assert claims.peer_id == "peer-1"
    assert claims.room_id == "room-1"
    assert claims.role == "creator"
    assert claims.jti


def test_two_tokens_have_different_jti() -> None:
    token_a = create_room_token(
        secret=SECRET, peer_id="p", room_id="r", role="creator", ttl_seconds=60
    )
    token_b = create_room_token(
        secret=SECRET, peer_id="p", room_id="r", role="creator", ttl_seconds=60
    )
    a = decode_room_token(secret=SECRET, token=token_a)
    b = decode_room_token(secret=SECRET, token=token_b)
    assert a.jti != b.jti


def test_decode_rejects_wrong_secret() -> None:
    token = create_room_token(
        secret=SECRET, peer_id="p", room_id="r", role="creator", ttl_seconds=60
    )
    with pytest.raises(TokenError):
        decode_room_token(secret="b" * 32, token=token)


def test_decode_rejects_expired_token() -> None:
    token = create_room_token(
        secret=SECRET, peer_id="p", room_id="r", role="creator", ttl_seconds=-1
    )
    with pytest.raises(TokenError, match="invalid room token"):
        decode_room_token(secret=SECRET, token=token)


def test_decode_rejects_wrong_audience() -> None:
    payload = {
        "sub": "p",
        "room": "r",
        "role": "creator",
        "aud": "some-other-audience",
        "iat": int(time.time()),
        "exp": int(time.time()) + 60,
        "jti": "x",
    }
    token = jwt.encode(payload, SECRET, algorithm=ALGORITHM)
    with pytest.raises(TokenError):
        decode_room_token(secret=SECRET, token=token)


def test_decode_rejects_alg_none() -> None:
    """Accepting whatever `alg` a token claims would let an attacker forge one."""
    payload = {
        "sub": "p",
        "room": "r",
        "role": "creator",
        "aud": AUDIENCE,
        "iat": int(time.time()),
        "exp": int(time.time()) + 60,
        "jti": "x",
    }
    forged = jwt.encode(payload, "", algorithm="none")
    with pytest.raises(TokenError):
        decode_room_token(secret=SECRET, token=forged)


def test_decode_rejects_missing_required_claim() -> None:
    payload = {
        "sub": "p",
        "room": "r",
        # "role" missing
        "aud": AUDIENCE,
        "iat": int(time.time()),
        "exp": int(time.time()) + 60,
        "jti": "x",
    }
    token = jwt.encode(payload, SECRET, algorithm=ALGORITHM)
    with pytest.raises(TokenError):
        decode_room_token(secret=SECRET, token=token)


def test_decode_rejects_invalid_role_value() -> None:
    payload = {
        "sub": "p",
        "room": "r",
        "role": "admin",
        "aud": AUDIENCE,
        "iat": int(time.time()),
        "exp": int(time.time()) + 60,
        "jti": "x",
    }
    token = jwt.encode(payload, SECRET, algorithm=ALGORITHM)
    with pytest.raises(TokenError, match="role"):
        decode_room_token(secret=SECRET, token=token)


def test_decode_garbage_string_raises() -> None:
    with pytest.raises(TokenError):
        decode_room_token(secret=SECRET, token="not-a-jwt-at-all")
