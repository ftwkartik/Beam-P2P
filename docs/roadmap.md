# Beam — Implementation Roadmap

Each milestone:

1. **Brief** before coding: what, why, files, decisions, tests.
2. **Implement** in small steps.
3. **Quality gate** green (ruff, mypy, pytest; eslint, tsc, vitest; Playwright once it exists). Failures are fixed, not skipped.
4. **Commit** in Conventional Commits format. Nothing is pushed unless asked.

Prerequisites: Docker Engine with the compose plugin, Node.js 20+ (present), and uv (installed in M1). **No API keys
or paid services are needed.** Secrets such as `JWT_SECRET`, `CODE_PEPPER` and `TURN_SECRET` are random strings
generated locally (`make secrets` writes them into `.env`).

| # | Milestone | Scope | Exit criteria |
|---|---|---|---|
| 1 | Skeleton | uv workspace (`protocol`, `server`, `cli`), server app factory, settings, structlog, error envelope, request IDs, `/health` `/ready`; Vite + React + TS scaffold; Dockerfiles, compose (nginx, server, redis), pre-commit, CI | `docker compose up --build` healthy; the gate passes locally and in CI |
| 2 | Protocol package | Signaling + peer message models, frame codec, code format and wordlist, SAS, constants; JSON Schema export → generated TS types; golden vectors | Python tests and vectors pass; the TS generation step works |
| 3 | Rooms API | Nameplate allocation, HMAC codes, join with the Lua script, burn-after-5, TTLs, JWT room tokens, rate limiter, trusted-proxy IP | Room integration tests, including concurrency and TTL-sweep assertions |
| 4 | Signaling WebSocket | `hello` auth, session state machine, relay, presence, heartbeats, reconnect grace, limits, Origin check, close codes | Signaling integration test suite |
| 5 | Multi-instance + observability | Redis pub/sub fan-out, per-instance subscription refcounts, two replicas behind nginx, Prometheus metrics | Cross-instance tests; `/metrics` exposes the defined series |
| 6 | TURN | coturn service + configs (dev and prod templates), ICE-servers endpoint with ephemeral credentials, hardening | Credential tests; coturn rejects invalid credentials and denied peers |
| 7 | Web: connection | Signaling client with reconnect, peer connection with perfect negotiation + ICE restart, negotiated channels, SAS display, create/join UI, share link + QR | Vitest units; manual two-tab connection |
| 8 | Web: transfer engine | Manifest + consent, framing, backpressure, block hashing, OPFS worker storage, progress/ETA, multi-file/folders, save | Vitest units; manual 1 GB transfer with bounded memory |
| 9 | Resume + E2E | Persisted bitmaps, resume after drop and after receiver reload, nack/retry; Playwright suite (scenarios 1–7) | Playwright green in CI (Chromium) |
| 10 | Python CLI | Typer CLI with `send` / `receive`, aiortc peer, safe filesystem storage, rich progress, resume; interop tests | CLI↔CLI and CLI↔browser tests pass |
| 11 | Advanced (pick by value) | **(a)** accounts + metadata history (PostgreSQL, Alembic, JWT + refresh rotation, isolation tests), or **(b)** encrypted relay fallback (MinIO, client-side AES-GCM, expiring links, cleanup), plus relay-only privacy mode | Its own test suite; docs updated |
| 12 | Measure + document | Benchmarks, security review pass, README, performance.md, project-report.md, interview-guide.md, ADR refresh, fresh-clone verification | Clone → `cp .env.example .env` → `docker compose up --build` → transfer works |

## Final validation checklist (Milestone 12)

Room create/join, the wrong-code burn, cross-instance signaling, TURN relay transfer, a large-file transfer with
bounded memory, integrity failure handling, resume (after a drop and after a reload), SAS match and mismatch,
CLI↔browser interop, all tests, lint, type checks, Docker from a clean clone, and README accuracy. Each item is ticked
off in `docs/project-report.md` with how it was verified.
