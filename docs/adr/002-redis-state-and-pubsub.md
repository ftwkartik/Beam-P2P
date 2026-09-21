# ADR-002: Redis for ephemeral state and cross-instance fan-out; PostgreSQL only for durable data

- **Status:** Accepted
- **Date:** 2026-08-06

## Context
A single in-process map of rooms cannot restart without losing them and cannot run on more than one instance. Beam's
MVP state (rooms, presence, attempt counters, rate limits) is short-lived and naturally expressed with TTLs. Peers of
one room may connect to different server instances.

## Decision
- **Redis** holds all shared MVP state with TTLs on every key. Multi-key transitions (join, burn, close) use Lua scripts for atomicity.
- **Redis pub/sub** channel per room. Each instance subscribes to rooms that have local peers and delivers to addressed local sessions.
- **No sticky sessions.** Any instance can serve any peer.
- **PostgreSQL is deferred** until durable data exists (the Advanced accounts/history milestone).

## Alternatives
- **In-memory + sticky sessions:** simple, but breaks when both peers land on different instances and loses state on deploy.
- **PostgreSQL for rooms + LISTEN/NOTIFY:** durable, but TTL expiry needs a sweeper, NOTIFY payloads are limited to
  8 KB (SDP can be larger), and write load is spent on data nobody needs tomorrow.
- **NATS / RabbitMQ for fan-out:** excellent messaging, but an extra service when Redis already covers state + pub/sub + rate limiting.

## Trade-offs
- Redis pub/sub is at-most-once. A message published while an instance is resubscribing can be lost. Mitigation:
  WebRTC negotiation is idempotent and retriable (ICE restart, re-offer on timeout), and the clients' protocol tolerates loss.
- Redis becomes a single point of failure. Mitigation: `/ready` reports it, and Sentinel or managed Redis is the production answer. Established P2P transfers are unaffected by a Redis outage.

## Consequences
- The multi-instance test (two servers, one Redis) is part of CI.
- Adding Postgres later touches only the new accounts/history modules. Room logic stays in Redis.
