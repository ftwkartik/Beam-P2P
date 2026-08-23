"""Per-connection state: the live session object and its message-rate limiter.

`PeerSession` wraps one authenticated WebSocket connection. It is deliberately a thin
holder of connection-scoped state (the socket, who they are, per-connection counters)
-- the actual registry of "who's connected right now" and the relay logic that uses
these sessions live in `services.signaling.SignalingHub`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from fastapi import WebSocket

from beam_server.security.tokens import Role


@dataclass(slots=True)
class TokenBucket:
    """A simple token-bucket limiter for per-connection message rate limiting
    (docs/protocol.md §2, "Message limits": 20 msgs/s, burst 60).
    """

    rate_per_second: float
    burst: int
    _tokens: float = field(init=False)
    _last_refill: float = field(init=False, default_factory=time.monotonic)

    def __post_init__(self) -> None:
        self._tokens = float(self.burst)

    def try_consume(self, amount: float = 1.0) -> bool:
        """Attempt to spend `amount` tokens. Returns whether there were enough."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._last_refill = now
        self._tokens = min(self.burst, self._tokens + elapsed * self.rate_per_second)

        if self._tokens < amount:
            return False
        self._tokens -= amount
        return True


@dataclass(slots=True)
class PeerSession:
    """One authenticated, live WebSocket connection."""

    websocket: WebSocket
    peer_id: str
    room_id: str
    role: Role
    client_kind: str
    client_version: str
    rate_limiter: TokenBucket
    ice_candidate_count: int = 0
