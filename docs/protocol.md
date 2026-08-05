# Beam — Wire Protocol (v1)

Two protocols are defined here:

1. **Signaling**: JSON over WebSocket between each client and the server.
2. **Peer protocol**: messages between the two peers over WebRTC data channels. JSON on the `control` channel and
   binary frames on the `data` channel.

All JSON messages are Pydantic models in `beam_protocol`, discriminated by `type`. TypeScript types are generated from
them. Unknown fields are rejected (`extra="forbid"`), and unknown `type` values end the session with a protocol error.

## 1. Room codes

Format: `<nameplate>-<word>-<word>-<word>`, e.g. `7-orbit-lantern-tiger`.

- **Nameplate:** a small integer allocated by the server (1–999, widening to 9999 under load). It identifies the room
  and is **not secret**.
- **Words:** 3 words chosen with `secrets.choice` from the EFF large wordlist (7,776 words), giving log2(7776³) ≈
  38.8 bits. They are generated server-side in the MVP and stored only as `HMAC-SHA256(pepper, room_id || words)`.
- Input is normalized before comparison: lowercase, whitespace and hyphens collapsed, and autocompletion against the
  wordlist in the UI and CLI.
- **Share link:** `https://<host>/r#7-orbit-lantern-tiger`. The code sits in the URL **fragment**, so it never appears
  in server or proxy logs or in Referer headers.

## 2. REST API (`/api/v1`)

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/rooms` | none (rate limited per IP) | Create a room. Returns `{room_id, code, nameplate, expires_at, token}` |
| POST | `/rooms/join` | none (rate limited per IP and per nameplate) | Body `{code}`. Returns `{room_id, expires_at, token}` |
| GET | `/rooms/{room_id}/ice-servers` | Bearer room token | STUN and TURN URLs plus ephemeral credentials |
| DELETE | `/rooms/{room_id}` | Bearer room token (creator) | Close the room early |
| GET | `/health` | none | Liveness |
| GET | `/ready` | none | Redis reachable, config valid |
| GET | `/metrics` | internal network only | Prometheus |

**Room token:** a JWT (HS256, `alg` pinned) with claims `sub=peer_id`, `room=room_id`, `role=creator|joiner`, `iat`,
`exp` = room expiry (at most 24 h), `jti`, `aud="beam-signaling"`.

**Error envelope:**

```json
{"error": {"code": "ROOM_FULL", "message": "This room already has two peers.", "details": {}, "request_id": "…"}}
```

Codes include `INVALID_CODE` (404, deliberately indistinguishable from an unknown room), `ROOM_FULL` (409),
`ROOM_BURNED` (410), `ROOM_EXPIRED` (410), `RATE_LIMITED` (429 with `Retry-After`), `VALIDATION_ERROR` (422),
`UNAUTHORIZED` (401) and `INTERNAL_ERROR` (500).

## 3. Signaling over WebSocket (`/ws`)

### Connection lifecycle

1. The client opens `wss://<host>/ws`. Browsers send `Origin`, which must be in `ALLOWED_ORIGINS`. Non-browser
   clients (the CLI) send no Origin and rely on the token.
2. **The first message must be `hello`**, sent within 5 s. The token is sent in the message rather than the query
   string so it stays out of access logs.
3. The server replies `welcome`, or closes with a code from the table below.
4. Both sides send heartbeats: the server sends WS pings every 20 s, and a connection with no pong for 45 s is closed.
5. On disconnect, the peer is marked `reconnecting` for a **30 s grace period** before `peer_left` is published.

### Client → server

| type | fields | notes |
|---|---|---|
| `hello` | `v: 1`, `token`, `client: {kind: "web"|"cli", version}` | Must be first |
| `signal` | `data: SdpPayload | IcePayload` | Relayed to the other peer in the room |
| `leave` | — | Graceful exit, publishes `peer_left` immediately |

`SdpPayload = {kind: "description", description: {type: "offer"|"answer"|"pranswer"|"rollback", sdp: str ≤ 32 KiB}}`
`IcePayload = {kind: "candidate", candidate: {candidate: str ≤ 1 KiB, sdpMid, sdpMLineIndex, usernameFragment} | null}` (`null` = end of candidates)

### Server → client

| type | fields |
|---|---|
| `welcome` | `peer_id`, `room_id`, `role`, `polite: bool`, `peers: [{peer_id, role, state}]`, `expires_at` |
| `peer_joined` | `peer_id`, `role` |
| `peer_reconnecting` | `peer_id` |
| `peer_left` | `peer_id`, `reason: "left"|"timeout"|"closed"` |
| `signal` | `from: peer_id`, `data` |
| `error` | `code`, `message` (non-fatal, e.g. rate limited for one message) |

`polite` is assigned by the server (the joiner is polite), so perfect negotiation roles are never ambiguous.

### Close codes

| Code | Meaning |
|---|---|
| 1000 | Normal closure |
| 4400 | Malformed or unknown message |
| 4401 | Missing, invalid or expired token |
| 4403 | Origin not allowed |
| 4404 | Room no longer exists |
| 4408 | No `hello` within 5 s |
| 4409 | The same peer connected elsewhere (the older connection is replaced) |
| 4413 | Message too large (> 64 KiB frame) |
| 4429 | Message rate exceeded (token bucket: 20 msgs/s, burst 60) |

## 4. Peer protocol (data channels)

Two channels are negotiated in-band by the creator, with fixed IDs for determinism (`negotiated: true`):

| Channel | id | Options | Carries |
|---|---|---|---|
| `control` | 0 | reliable, ordered | JSON messages |
| `data` | 1 | reliable, **unordered** | Binary frames (self-describing, so ordering is unnecessary) |

### 4.1 Control messages (JSON)

| type | direction | fields |
|---|---|---|
| `hello` | both | `proto: 1`, `app_version`, `max_frame: int` (from SCTP `maxMessageSize`), `caps: [..]` |
| `offer_files` | S→R | `transfer_id`, `block_size` (1 MiB), `files: [{index, path, size, mime, mtime}]` |
| `accept` | R→S | `transfer_id`, `have: {file_index: bitmap_b64}` (verified blocks, possibly empty) |
| `decline` | R→S | `transfer_id`, `reason` |
| `block` | S→R | `file`, `index`, `sha256` (hex), sent **before** the block's frames |
| `ack` | R→S | `file`, `verified: [block ranges]` (batched, e.g. every 8 blocks or 250 ms) |
| `nack` | R→S | `file`, `index`, `reason: "hash_mismatch"` |
| `file_done` | S→R | `file`, `sha256` (whole file) |
| `file_verified` | R→S | `file`, `ok: bool` |
| `transfer_done` | S→R | `transfer_id` |
| `cancel` | both | `transfer_id`, `reason` |
| `error` | both | `code`, `message` |

**Validation on the receiver:** `path` is relative and slash-separated, with no `..`, no absolute paths, no drive
letters and no control characters, at most 255 bytes per segment and 4096 in total. There are at most 10,000 files,
sizes must be ≥ 0 and ≤ `2^53`, and the block size must be one of the allowed values.

### 4.2 Binary frame (data channel)

```
offset  size  field
0       2     magic 0x42 0x4D ("BM")
2       1     version = 1
3       1     flags (bit0 = last frame of block)
4       4     file index        (uint32, big-endian)
8       8     byte offset       (uint64, big-endian) within the file
16      n     payload           (n ≤ max_frame − 16; default frame = 64 KiB payload + 16 B header)
```

- Frames never cross block boundaries, so a block is `block_size / 65536` frames (16 for 1 MiB).
- The receiver writes each payload at `offset`. Duplicates (after a retry) are idempotent.
- The sender pauses when `bufferedAmount > 4 MiB` and resumes on `bufferedamountlow` (threshold 1 MiB).

### 4.3 Integrity

- The **block hash** is SHA-256 of the block's bytes, computed by the sender while reading the block from disk.
- The **file hash** is a root hash over the block hashes: `SHA-256("beam-file-v1" || size_u64 || h_0 || h_1 || … || h_n)`.
  A plain streaming SHA-256 would require in-order bytes. Blocks arrive unordered and resume skips blocks, so both
  sides compute the root from per-block hashes instead. On resume, the sender still reads and hashes skipped blocks
  (without sending them), so a file modified between sessions is detected. The receiver computes the root from its
  verified block hashes, which are persisted with the bitmap. A plain SHA-256 of the saved file is also shown in the UI
  for users comparing against published checksums. It is computed after completion, sequentially from disk.
- A mismatch sends `nack`. The block is re-sent at most 3 times, after which the transfer fails with `INTEGRITY_ERROR`.

### 4.4 Short authentication string (SAS)

```
input  = "beam-sas-v1" || room_id || min(fpA, fpB) || max(fpA, fpB)
         (fp = DTLS certificate fingerprint "sha-256 AB:CD:…" from each side's SDP, uppercased)
digest = SHA-256(input)
sas    = first 40 bits → 5 symbols × 8 bits → a 256-entry emoji/word table
```

Each peer computes the SAS from its **local** fingerprint and the **remote** fingerprint it actually negotiated with,
which it reads from `RTCPeerConnection` stats or the remote description. A man-in-the-middle holds different
certificates on each side, so the two SAS values differ unless the attacker finds a 40-bit collision in real time.
See security.md for why 40 bits was chosen.

### 4.5 Versioning

`hello.v` / `hello.proto` carry the major version. Minor, additive changes are negotiated via `caps`. A major mismatch
closes the session with a clear error. Golden vectors in `tests/vectors/` pin frame encoding, SAS derivation and code
normalization for both implementations.
