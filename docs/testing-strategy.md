# Beam — Testing Strategy

Every milestone ends with the full quality gate green:

- Python: `ruff check`, `ruff format --check`, `mypy --strict`, `pytest`.
- Web: `eslint`, `tsc --noEmit`, `vitest`.
- Playwright from Milestone 9 on.

No test depends on the public internet or on third-party STUN/TURN.

## Test pyramid

| Layer | Tooling | Scope | Needs |
|---|---|---|---|
| Unit (Python) | pytest | Protocol models, frame codec, code normalization, SAS, HMAC, tokens, rate-limit math, path safety, bitmap logic | Nothing |
| Unit (TS) | Vitest | Framing, bitmap, SAS, sender/receiver state machines with fake channels, resume logic, storage adapters (fake) | Nothing |
| Golden vectors | pytest + Vitest | `tests/vectors/*.json`: both languages must reproduce identical bytes for frames, SAS and code normalization | Nothing |
| Integration (server) | pytest + httpx + `websockets` | REST + WS against a real Redis: rooms, joins, burns, TTLs, relay, reconnect grace, limits, close codes | Redis (compose) |
| Multi-instance | pytest | Two uvicorn servers on different ports sharing Redis; peers on different instances complete signaling | Redis |
| Peer engine (Python) | pytest + aiortc | CLI sender ↔ CLI receiver over loopback: multi-file, resume after forced disconnect, corrupted block → nack → retry | Nothing extra (aiortc in-process) |
| E2E (browser) | Playwright (Chromium, Firefox) | Full stack via compose: two browser contexts create/join, SAS equal, transfer, verify, resume after `context.setOffline(true)` and after receiver reload | Compose stack |
| Interop | Playwright + CLI subprocess | Browser → CLI and CLI → browser transfers, verified by hash | Compose stack |
| TURN | Playwright + pytest | Force `iceTransportPolicy: "relay"`, and assert the selected candidate pair is `relay` and the transfer completes. coturn rejects bad credentials and denied peer IPs | coturn |
| Benchmarks | `bench/` scripts | Not pass/fail. Record numbers for docs | Compose stack |

## Key test cases

### Rooms and codes
- Code generation uses the wordlist and has the right format. Normalization handles case, spaces, hyphens and unicode confusables.
- Correct join → paired. A second joiner → `ROOM_FULL`. The 5th wrong attempt burns the room (410), after which the correct code also fails.
- Unknown nameplate and wrong words give an identical response body and status.
- Concurrent correct joins (asyncio.gather) → exactly one succeeds (Lua atomicity).
- TTLs are set on **every** key (a scan-based assertion in tests: no key without a TTL).
- Nameplate allocation under contention yields no duplicates, and the range widens when full.

### Signaling
- `hello` timeout → 4408. Bad token / expired / wrong `aud` → 4401. A disallowed Origin → 4403.
- Oversize message → 4413. Rate flood → 4429. Unknown type or extra fields → 4400.
- `signal` from A reaches only B, never a peer in another room (a negative test with 3 rooms).
- Disconnect → `peer_reconnecting`, reconnect within the grace period → no `peer_left`. After the grace period → `peer_left`.
- Same peer connecting twice → the older session is closed with 4409.
- Multi-instance: A on instance 1, B on instance 2, a full offer/answer/candidate exchange. Instance 2 is killed and
  B reconnects to instance 1 and continues.

### Transfer engine (Python and TS, shared scenarios)
- Frame encode/decode round trip. Rejection of bad magic or version and of out-of-range offsets.
- Block completion only when all bytes and the hash are present, in any arrival order.
- Hash mismatch → nack → retry succeeds. 3 mismatches → `INTEGRITY_ERROR`.
- Resume: a bitmap with random holes, where the sender sends exactly the missing blocks (asserted by counting frames).
- Sender-side file changed between sessions → root hash mismatch detected.
- Backpressure: the sender never exceeds the high watermark (fake channel with `bufferedAmount`).
- Path safety table: `../x`, `/etc/passwd`, `C:\x`, `a/../../b`, `CON`, NUL bytes and deep nesting are all rejected. Valid nested paths are accepted.
- Zero-byte files, a file of exactly one block, and a file that is one byte over a block boundary.

### E2E scenarios (Playwright)
1. Happy path: a 50 MB random file, hash verified on both sides.
2. Multi-file folder with nested paths.
3. Decline → the sender sees the decline and no OPFS data remains.
4. Network drop mid-transfer (`setOffline`) → automatic ICE restart/reconnect → completes, with fewer bytes re-sent than the full file.
5. Receiver reload mid-transfer → resume from the persisted bitmap.
6. SAS equality on both pages, plus a *negative* test: an injected fingerprint-rewriting proxy (test-only signaling
   middleware) → the SAS values differ.
7. Relay-only transfer through coturn.

Large-file memory test (manual and CI-optional): transfer 2 GB in Chromium, sample `performance.measureUserAgentSpecificMemory()`
or the process RSS, and assert memory stays bounded (not proportional to the file size).

## Fixtures and infrastructure

- `docker compose -f docker-compose.yml -f docker-compose.test.yml` provides Redis, coturn (test config) and the app for E2E.
- The server fixture creates the app with dependency overrides (fixed clock, small TTLs, test secrets). Redis DB 15 is flushed per test.
- A **fake clock** is injected into rate limiting and TTL logic, so tests never sleep for real durations.
- Random test data is seeded, and seeds are printed on failure.
- Playwright uses traces and videos on failure. Chromium flags `--use-fake-ui-for-media-stream` and
  `--disable-features=WebRtcHideLocalIpsWithMdns` make localhost ICE deterministic.

## Benchmarks (`bench/`)

| Script | Measures | Reported as |
|---|---|---|
| `signaling_load.py` | N simulated peer pairs (asyncio + websockets) doing the full hello/signal exchange against 1 and 2 instances | Connection setup p50/p95/p99, relay latency, maximum stable concurrent connections at a fixed CPU budget, errors |
| `transfer_throughput.ts` (Playwright) | Browser↔browser throughput for frame sizes 16, 64 and 256 KiB and block sizes 256 KiB, 1 MiB and 4 MiB, over loopback | MB/s median of 5 runs, plus CPU notes |
| `cli_throughput.py` | CLI↔CLI and CLI↔browser throughput | MB/s |

Results are written to `bench/results/*.json` and summarized in `docs/performance.md` with hardware details. Only
measured numbers appear in the README and résumé bullets.

## CI (GitHub Actions)

1. `python`: uv sync → ruff → mypy → pytest (unit + integration; Redis service container).
2. `web`: npm ci → eslint → tsc → vitest → build. A schema-drift check regenerates the TS types and fails on diff.
3. `e2e`: compose up (test override) → Playwright (Chromium; Firefox nightly job) → upload traces.
4. `docker`: build the images.
