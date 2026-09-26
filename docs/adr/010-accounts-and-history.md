# ADR-010: Accounts and transfer history (the Milestone 11 advanced option)

- **Status:** Accepted
- **Date:** 2026-09-25

## Context
docs/roadmap.md's Milestone 11 is "pick by value" between two advanced features: (a) accounts + metadata
history, or (b) an encrypted relay fallback for when direct P2P isn't reachable. Accounts was picked: it's the
smaller, more self-contained addition, and docs/data-model.md §3 already specified the schema in anticipation of
this choice, so the design work was largely already done.

## Decision
- **PostgreSQL**, added as an optional component: `Settings.database_url` defaults to empty, and leaving it unset
  means the accounts/history routes simply aren't registered (see main.py) -- a deployment that doesn't want
  accounts never opens a DB connection pool or exposes an endpoint that would 500 on every call.
- **Schema**: `users`, `refresh_tokens`, `transfers`, `transfer_files` (docs/data-model.md §3), managed by Alembic
  with no `create_all` in the app itself. The `devices` table that schema sketches is deferred -- nothing in this
  milestone's actual scope (accounts + history) depends on device registration, and adding it would be scope the
  roadmap's exit criterion doesn't ask for.
- **Password hashing**: argon2id via `argon2-cffi`, the OWASP default.
- **Two different token shapes**: a short-lived **access token** (JWT, HS256, the same pinned-algorithm pattern as
  room tokens, a different `aud` so one can never be replayed as the other) avoids a DB round trip on every
  authenticated request. A **refresh token** is opaque and DB-backed instead, rotated on every use with reuse
  rejected -- a JWT refresh token would only be revocable by waiting out its own expiry, which defeats the point
  of the token whose job is recovering from theft.
- **History is metadata only, written by the client** after a transfer has already finished on its own
  (docs/data-model.md §3) -- the server never sees file contents, consistent with the P2P design everywhere else
  in this project.
- **IDs are plain `uuid4`**, not the `uuid7` docs/data-model.md mentions: Python 3.12's stdlib has no UUIDv7
  generator (that lands in 3.14), and a dependency just for time-sortable primary keys isn't worth it when
  `created_at` columns already give query-time ordering.

## Alternatives
- **Session cookies instead of JWT + refresh tokens:** simpler, but doesn't suit the CLI (no cookie jar) and this
  project already has an established, documented JWT pattern (ADR-003) to extend rather than introduce a second
  auth mechanism alongside.
- **Full reuse-detection with refresh-token families** (revoking every other token from a session the instant one
  member of its family is replayed): real additional protection, genuinely worth having eventually, but the
  single-token rotation-and-reject-on-reuse check is what actually matters for this scope -- the family bookkeeping
  is extra machinery this pass doesn't need to ship to meet the exit criterion.
- **The encrypted relay fallback (roadmap option b):** a bigger addition (MinIO, client-side AES-GCM, expiring
  links, a cleanup job, a relay-only privacy mode) that solves a different problem (transfers when direct P2P and
  TURN both fail) than accounts does. Left for a future pass if ever needed; not something this project's current
  shape calls for.

## Trade-offs
- A developer who only wants the P2P transfer feature now also sees a `postgres` service in `docker-compose.yml`.
  Mitigated by making it fully optional at the application layer (unset `DATABASE_URL`, nothing changes) even
  though the compose file still brings the container up by default for convenience.
- No web client UI for registering, logging in, or viewing history yet -- this pass is the backend API and its own
  test suite (the roadmap's literal exit criterion), not the full user-facing feature. Documented as a deliberate
  scope cut, not a silent gap: the API is real, tested against a real Postgres including the cross-user isolation
  matrix, and ready for a client to call.

## Consequences
- Another user's data is unreachable through this API, not merely hidden behind a check a handler could forget to
  make -- `TransferRepository`'s methods all take `owner_id` and filter on it, matching docs/security.md's
  isolation rule for user-facing data everywhere else in this project.
- Two real bugs surfaced only by actually running the full flow against a real Postgres (a timezone-naive/aware
  datetime mismatch, and a missing eager-load causing an async lazy-load outside a valid context) -- neither was
  caught by ruff or mypy --strict, underscoring why this project's convention is to verify against the real thing
  before calling a milestone done, not just pass static checks.
