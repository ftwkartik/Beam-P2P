# ADR-009: Short authentication string (SAS) to detect signaling MITM

- **Status:** Accepted
- **Date:** 2026-08-06

## Context
WebRTC encrypts data channels with DTLS, but peers learn each other's certificate fingerprints *through the
signaling server*. A malicious or compromised server could substitute its own fingerprints and man-in-the-middle the
connection invisibly. [redacted] has no defense against this.

## Decision
Both peers derive a **40-bit SAS** from `room_id` and the sorted pair of DTLS fingerprints they *actually*
negotiated, and show it as 5 emoji with names. Users compare them out of band (same room, phone, chat). A mismatch
aborts. The derivation is pinned by golden vectors in Python and TS.

## Alternatives
- **Trust the server:** the status quo in most WebRTC apps. Not acceptable for a privacy-first product.
- **Short numeric SAS (4–6 digits):** vulnerable to real-time certificate grinding (security.md §5).
- **PAKE (SPAKE2, RFC 9382) keyed by the code words:** automatic, with no user comparison and the server never seeing
  the words. It is the best option, and the documented next step. It is deferred because it needs a careful
  two-language implementation with RFC test vectors.

## Trade-offs
- It depends on users actually comparing. The UI makes this prominent, but it cannot force it.
- 40 bits balances grinding resistance against how many symbols a person will compare.

## Consequences
- An active-attack test (a fingerprint-rewriting test proxy) is part of the E2E suite, and it proves the SAS diverges.
