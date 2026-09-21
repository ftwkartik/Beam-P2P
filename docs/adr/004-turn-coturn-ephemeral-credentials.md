# ADR-004: Self-hosted coturn with ephemeral HMAC credentials

- **Status:** Accepted
- **Date:** 2026-08-06

## Context
Direct WebRTC connections fail behind symmetric NATs and restrictive firewalls. STUN alone (even a public server)
cannot help those users connect at all. A TURN server relays traffic, which costs bandwidth, so it must not be usable
by arbitrary parties or as a network pivot.

## Decision
Run **coturn** in compose. The API mints **time-limited credentials** (TURN REST API convention, HMAC-SHA1 with a
shared secret) only for valid room tokens. Harden coturn with `denied-peer-ip` for all private, loopback, link-local
and CGNAT ranges, plus quotas, bandwidth caps, and no CLI. Offer UDP, TCP and (in production) TLS on 5349.
Clients expose the selected candidate pair type (host / srflx / relay) in the UI and metrics.

## Alternatives
- **STUN only:** free, but a meaningful fraction of users cannot connect.
- **Static TURN username/password:** leaks from the client bundle and becomes an open relay.
- **Managed TURN (Twilio, Cloudflare Calls, Metered):** easy, but paid and API-key-bound, and not reproducible locally.
- **Relaying through the signaling server (WebSocket relay):** it puts bytes through Python and loses the WebRTC transport benefits.

## Trade-offs
- An extra service with UDP port ranges, which is awkward in Docker (host networking or published port ranges).
  The dev compose publishes a small relay range.
- Relayed sessions consume server bandwidth. Quotas bound the damage.

## Consequences
- Transfers succeed on restrictive networks, and relay usage is observable.
- The SSRF-via-TURN risk is explicitly closed and tested.
