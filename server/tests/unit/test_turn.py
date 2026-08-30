"""Ephemeral TURN credential minting (docs/adr/004-turn-coturn-ephemeral-credentials.md;
docs/security.md §3, "TURN hardening").
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from unittest.mock import patch

from beam_server.services.turn import mint_turn_credentials

SECRET = "s" * 32
FROZEN_TIME = 1_700_000_000.0


def test_username_embeds_expiry_and_peer_id() -> None:
    with patch("beam_server.services.turn.time.time", return_value=FROZEN_TIME):
        creds = mint_turn_credentials(
            secret=SECRET, peer_id="peer-1", ttl_seconds=3600, urls=["turn:example:3478"]
        )
    assert creds.username == f"{int(FROZEN_TIME) + 3600}:peer-1"


def test_credential_matches_turn_rest_api_hmac_sha1() -> None:
    creds = mint_turn_credentials(
        secret=SECRET, peer_id="peer-1", ttl_seconds=3600, urls=["turn:example:3478"]
    )
    expected = base64.b64encode(
        hmac.new(SECRET.encode(), creds.username.encode(), hashlib.sha1).digest()
    ).decode()
    assert creds.credential == expected


def test_credential_depends_on_the_secret() -> None:
    with patch("beam_server.services.turn.time.time", return_value=FROZEN_TIME):
        a = mint_turn_credentials(secret=SECRET, peer_id="peer-1", ttl_seconds=3600, urls=[])
        b = mint_turn_credentials(secret="t" * 32, peer_id="peer-1", ttl_seconds=3600, urls=[])
    assert a.username == b.username
    assert a.credential != b.credential


def test_ttl_and_urls_are_carried_through() -> None:
    creds = mint_turn_credentials(
        secret=SECRET, peer_id="p", ttl_seconds=120, urls=["turn:a", "turn:b"]
    )
    assert creds.ttl_seconds == 120
    assert creds.urls == ["turn:a", "turn:b"]
