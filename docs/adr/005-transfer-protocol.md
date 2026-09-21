# ADR-005: Self-describing binary frames, per-block hashes, peer-held resume state

- **Status:** Accepted
- **Date:** 2026-08-06

## Context
A JSON header followed by a separate binary message per chunk is ambiguous once two transfers interleave on the same
channel, and acknowledging every chunk through the server puts signaling load in the data path. Resume state kept
only in server memory is lost on restart, and without hashes a truncated transfer can look complete.

## Decision
- **Two negotiated data channels:** `control` (reliable, ordered, JSON) and `data` (reliable, unordered, binary).
- **Frames carry a 16-byte header** (magic, version, flags, file index, byte offset), so each frame is self-describing and order-independent.
- **Default sizes: 64 KiB frame payload, 1 MiB block.** Frames never span blocks.
- **Per-block SHA-256** is announced on `control` before the block, verified by the receiver, and retried on
  mismatch. The **file root hash** is taken over all block hashes.
- **Resume state lives with the receiver** (manifest + verified-block bitmap). On reconnect it sends `accept{have}`
  and the sender sends only missing blocks. Acks go peer-to-peer and are batched.
- Backpressure via `bufferedAmount` with 4 MiB high / 1 MiB low watermarks.

## Alternatives
- **Unordered + unreliable with application retransmit:** potentially faster on lossy links, but reimplements SCTP reliability. Not justified without measurements.
- **One channel per file:** parallelism is illusory over a single SCTP association and complicates ordering and limits.
- **Whole-file streaming SHA-256:** needs in-order data and cannot resume without re-reading everything, so the root-over-blocks design is used instead.
- **Merkle tree:** enables verifying partial ranges from untrusted sources (BitTorrent v2). Unnecessary with a single authenticated sender.

## Trade-offs
- The 16-byte header costs 0.02% of each 64 KiB frame. Negligible.
- Hashing costs CPU (the browser uses hash-wasm, the CLI uses hashlib). The benchmark checks it isn't the bottleneck.
- Frame and block sizes are tunable, and the benchmark compares 16, 64 and 256 KiB frames and 256 KiB–4 MiB blocks. This ADR is revisited if the results favour other defaults.

## Consequences
- Signaling load is independent of file size. The server never participates in transfers.
- Resume survives server restarts and receiver reloads.
- Golden vectors pin frame encoding across Python and TypeScript.
