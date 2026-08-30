"""Ephemeral TURN credential minting (docs/adr/004-turn-coturn-ephemeral-credentials.md;
docs/security.md §3).

coturn's `use-auth-secret` mode (the "TURN REST API" convention) needs no database or
provisioning step: any `username`/`credential` pair coturn can verify against the
shared secret is accepted, so minting one is pure computation here. The username
embeds its own expiry, which is how coturn enforces the lifetime without state.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TurnCredentials:
    username: str
    credential: str
    ttl_seconds: int
    urls: list[str]


def mint_turn_credentials(
    *,
    secret: str,
    peer_id: str,
    ttl_seconds: int,
    urls: list[str],
) -> TurnCredentials:
    """Mint a coturn `use-auth-secret` credential valid for `ttl_seconds`, scoped to
    `peer_id` (embedded in the username so credential use is attributable in coturn's
    own logs, though coturn itself does not enforce the peer-id part).
    """
    expiry = int(time.time()) + ttl_seconds
    username = f"{expiry}:{peer_id}"
    digest = hmac.new(secret.encode(), username.encode(), hashlib.sha1).digest()
    credential = base64.b64encode(digest).decode()
    return TurnCredentials(
        username=username,
        credential=credential,
        ttl_seconds=ttl_seconds,
        urls=urls,
    )
