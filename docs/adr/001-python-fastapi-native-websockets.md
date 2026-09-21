# ADR-001: Python/FastAPI signaling server over native WebSockets

- **Status:** Accepted
- **Date:** 2026-08-06

## Context
The server authenticates peers, manages rooms, relays SDP and ICE messages, and mints TURN credentials. The work is
I/O-bound, with many long-lived connections and small messages. Clients are browsers **and** a Python CLI.

## Decision
Use **FastAPI** (Starlette WebSockets on uvicorn) with a **custom JSON protocol** defined as Pydantic v2 discriminated
unions in the shared `beam_protocol` package. The protocol is versioned (`hello.v`), size-limited and strict (`extra="forbid"`).

## Alternatives
- **Node + Socket.IO:** mature, but it moves the backend out of Python, and the Socket.IO wire protocol needs a
  Socket.IO client in every language.
- **python-socketio:** keeps Python but inherits Socket.IO's opaque protocol and its own room and manager abstractions,
  which overlap with our Redis design.
- **Django Channels:** capable, but heavier (ASGI + channel layers + Django) for a service with no ORM needs in the MVP.
- **aiohttp:** fine for WebSockets, but weaker validation, OpenAPI and dependency-injection ergonomics than FastAPI.

## Trade-offs
- Reconnection, acknowledgements and rooms are implemented ourselves. They are small and fully tested, and they make the design explicit.
- A Python server handles fewer connections per core than Go or Rust. Signaling traffic is tiny and horizontally scalable (ADR-002), and the benchmark quantifies it.

## Consequences
- One protocol definition serves the server, the CLI and (via generated TS types) the web client.
- OpenAPI documents the REST part automatically. The WebSocket protocol is documented in `docs/protocol.md`.
