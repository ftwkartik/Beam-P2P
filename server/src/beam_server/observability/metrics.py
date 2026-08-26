"""Prometheus metrics (docs/roadmap.md Milestone 5; docs/protocol.md's `/metrics`).

A small, deliberately curated set: docs/security.md's "Do not add tools purely for
decoration" applies to metrics as much as to infrastructure. Each one answers a
specific operational question this project cares about being able to answer -- how
many peers are connected right now, is signaling relay working, are rooms being
guessed at -- rather than instrumenting everything reachable.

One process-wide `CollectorRegistry` is used (not the client library's implicit
default registry), so multiple app instances created in the same test process (see
the cross-instance tests) don't collide by registering the same metric name twice.
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge

registry = CollectorRegistry()

ws_connections_active = Gauge(
    "beam_ws_connections_active",
    "WebSocket signaling connections currently open on this instance.",
    registry=registry,
)

ws_connections_total = Counter(
    "beam_ws_connections_total",
    "WebSocket signaling connections accepted (authenticated hello succeeded).",
    registry=registry,
)

ws_close_total = Counter(
    "beam_ws_close_total",
    "WebSocket connections closed, by close code.",
    ["code"],
    registry=registry,
)

rooms_created_total = Counter(
    "beam_rooms_created_total",
    "Rooms created via POST /rooms.",
    registry=registry,
)

room_join_attempts_total = Counter(
    "beam_room_join_attempts_total",
    "Room join attempts, by outcome.",
    ["outcome"],  # joined | invalid_code | room_full | room_burned
    registry=registry,
)

signal_messages_relayed_total = Counter(
    "beam_signal_messages_relayed_total",
    "Signal messages published for relay to another peer.",
    registry=registry,
)
