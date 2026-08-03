# Beam — Product Definition

> **Beam** sends files directly between devices, browser to browser or terminal to browser, over an encrypted
> peer-to-peer connection. Transfers are verified, resumable, and limited by disk space rather than memory.

"Beam" is a working name. The display name comes from the `APP_NAME` setting and the web client's `VITE_APP_NAME`.

## Problem

Sending a large file to someone usually means uploading it to a third-party cloud service and waiting twice (up, then
down). Size limits, retention policies and privacy all get in the way. WebRTC lets two devices talk directly, but
naive implementations break down in practice: large files crash the tab, corporate NATs block connections, a dropped
Wi-Fi connection restarts the whole transfer, nothing proves the file arrived intact, and the signaling server could
silently intercept the connection.

## Core user experience

1. **Alice** opens Beam and clicks *Send*, then picks files or a folder. She gets a code such as `7-orbit-lantern-tiger`
   and a link with a QR code.
2. **Bob** opens the link, or types the code into the web app or `beam receive 7-orbit-lantern-tiger` in a terminal.
3. The two peers connect directly. If direct fails, they connect through Beam's TURN relay, which only ever sees
   encrypted bytes. Both screens show the **same 4-emoji verification code**. If they match (compared over the phone or
   side by side), nobody is in the middle.
4. Bob sees the file list and sizes and clicks **Accept** (the terminal asks `Accept 3 files (4.2 GB)? [y/N]`).
5. Files stream to disk with live speed, ETA and connection type (direct or relayed). Every 1 MiB block is
   SHA-256-verified as it arrives.
6. Alice's Wi-Fi drops halfway. When she reconnects, the transfer **resumes from the last verified block**. Bob can even
   reload his tab without losing progress.
7. Bob gets a "verified" badge per file (the file root hash, computed over all block hashes, matches), then saves or opens the files.

## Feature tiers

### MVP (Milestones 1–10)

- **Rooms:** create/join with high-entropy word codes and share links/QR. Two peers per room. Rooms expire.
- **Anonymous by default:** no account needed. Short-lived signed room tokens authorize the WebSocket.
- **Signaling:** FastAPI native WebSocket, typed and versioned protocol, heartbeats, reconnect grace period.
- **Horizontal scale:** Redis-backed state plus pub/sub, so peers on different server instances can meet.
- **NAT traversal:** STUN plus a bundled **coturn TURN** server with ephemeral credentials. ICE restart on failure.
  Connection-type indicator.
- **Transfer engine:** multi-file and folder transfer, receiver consent, binary framing, backpressure, streaming to
  disk (OPFS), per-block SHA-256 verification plus a file root hash.
- **Resume:** after a disconnect, and after a receiver page reload.
- **Verification codes (SAS)** derived from both DTLS fingerprints.
- **Python CLI peer** (`beam send` / `beam receive`, built on aiortc) that is interoperable with the browser client.
- **Operations:** structured logs, `/health`, `/ready`, Prometheus metrics, Docker compose, CI.

### Advanced (Milestone 11, after the core is stable)

- **Accounts and transfer history** (optional sign-in; saved devices; metadata-only history of names, sizes, hashes and
  outcome, never contents). Introduces PostgreSQL and Alembic.
- **Encrypted relay fallback:** when P2P is impossible or the recipient is offline, the sender encrypts client-side and
  uploads to S3-compatible storage (MinIO) behind an expiring one-time link. The server never sees plaintext or keys
  (the key is in the URL fragment).
- **Relay-only privacy mode:** force TURN so the peer never learns your IP address.
- **One-to-many:** one sender, several receivers (star topology from the sender).
- Streaming ZIP download for folders.

### Stretch (designed for, not planned)

Mobile PWA share target, LAN-only discovery (mDNS), transfer speed tests, desktop tray app, end-to-end encrypted chat in rooms.

## Comparison

| Capability | [redacted] | Beam |
|---|---|---|
| Backend | Node/Express/Socket.IO, one file | Python/FastAPI native WebSocket, layered |
| State | Process memory | Redis with TTLs (+ Postgres for accounts, Advanced) |
| Multiple server instances | ✗ | ✓ Redis pub/sub fan-out, tested with 2 replicas |
| Room code | 8 chars, unthrottled | Number + 3 EFF words (~45 bits), hashed at rest, rate-limited, expiring |
| Auth to signaling | Anonymous cookie | Signed short-lived room token (first WS message) |
| NAT traversal | STUN only | STUN + TURN (ephemeral creds) + ICE restart |
| Large files | RAM-bound (whole file in memory) | Streamed to disk (OPFS / filesystem) |
| Integrity | None | Per-block SHA-256 + file root hash |
| Resume | Server-memory ack table | Peer-to-peer, survives server restart and receiver reload |
| Consent | Auto-accept | Accept/decline with a preview |
| MITM detection | ✗ | Emoji SAS from DTLS fingerprints |
| Clients | Browser | Browser + Python CLI (interoperable) |
| Tests / CI / Docker | ✗ | pytest, Vitest, Playwright E2E, benchmarks, compose, GitHub Actions |
| Observability | console.log | structlog, health/ready, Prometheus |

## Non-goals

- Beam is not cloud storage. There is no persistence of file contents on the server in the MVP.
- No accounts are required, ever, for basic transfers.
- No server-side scanning of content. The server never sees it.
- Not a chat or collaboration app.
