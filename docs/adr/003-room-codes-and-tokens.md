# ADR-003: Word-based room codes, HMAC at rest, burn-after-failures, short-lived room tokens

- **Status:** Accepted
- **Date:** 2026-08-06

## Context
The code is the only thing a recipient needs, so it must be easy to read aloud and type, yet resistant to guessing.
[redacted] uses 8 random characters with unlimited join attempts and cookie identity.

## Decision
- Code = public **nameplate** (small integer) + **3 EFF-wordlist words** (~38.8 bits of secret).
- Store `HMAC-SHA256(pepper, room_id‖words)`. Compare in constant time.
- **Burn the room after 5 failed attempts.** Per-IP join and create rate limits.
- On success, issue a **JWT room token** (peer-bound, room-bound, `aud`, expiry = room expiry). It is presented in the WebSocket `hello` and to the ICE-servers endpoint.
- Share links put the code in the URL fragment.

## Alternatives
- **Random alphanumerics ([redacted]):** hard to read aloud, with worse ergonomics for the same entropy.
- **Numeric PINs:** far too little entropy unless attempts are tiny, and poor UX at safe lengths.
- **Cookies/sessions for identity:** implicit and CSRF/CSWSH-prone. They don't work well for the CLI.
- **PAKE (SPAKE2) immediately:** the strongest option (the server never learns the words), but it needs a careful
  implementation in two languages. It is documented as the upgrade path (security.md §1).

## Trade-offs
- The server knows the full code in the MVP (it generates it). It is honest-but-curious by assumption, and the SAS covers active attacks.
- Burning rooms lets an attacker grief a specific nameplate by guessing wrong 5 times. Accepted: the users simply create a new room, and per-IP limits make this costly at scale.

## Consequences
- Guessing success probability per room is at most 5/7776³, a figure that can be stated plainly.
- The CLI and web share code normalization and wordlist autocompletion (golden vectors).
