# E2E suite (Milestone 9)

Runs against the real `docker-compose.yml` stack (nginx, two server replicas, Redis,
coturn) in a real headless Chromium, per `docs/testing-strategy.md`'s "E2E scenarios
(Playwright)" list.

## Running

```sh
cd /home/guts/P2P
cp .env.example .env && ./scripts/gen_secrets.sh   # once
docker compose up --build -d
cd web
PW_NO_SANDBOX=1 npx playwright test                 # PW_NO_SANDBOX only needed in
                                                     # sandboxed dev environments
                                                     # without user namespaces
```

`playwright.config.ts`'s `webServer` reuses an already-running stack locally
(`reuseExistingServer: !process.env.CI`); CI always starts one fresh.

## Coverage

| # | Scenario | File |
|---|---|---|
| 1 | Happy path: a multi-megabyte file, hash verified, SAS equality | `happy-path.spec.ts` |
| 2 | Multi-file folder with nested paths | `multi-file.spec.ts` |
| 3 | Decline: sender is told, receiver shows no save link | `decline.spec.ts` |
| 4 | Network drop mid-transfer (`context.setOffline`) → resumes | `resume.spec.ts` |
| 5 | Receiver reload mid-transfer → resumes from the persisted bitmap | `resume.spec.ts` |
| 6 (equality only) | SAS matches on both sides | asserted inside `happy-path.spec.ts` |
| 7 | Relay-only transfer forced through coturn | `turn-relay.spec.ts` |

**Deliberately not built: scenario 6's negative half** (an injected fingerprint-
rewriting proxy proving the SAS *diverges* under a real interception). Naively
rewriting the fingerprint in the relayed SDP just breaks the DTLS handshake — browsers
verify the peer's certificate against the fingerprint declared in the SDP, so a
mismatched value fails the connection outright rather than producing a *working but
mismatched* one. A faithful test needs an actual person-in-the-middle: a relay that
terminates two separate, real DTLS sessions (one to each browser) using a different
certificate on each leg, forwarding application data between them, so each victim
completes a genuine connection but computes its SAS from the attacker's fingerprint
instead of the real peer's. That's a standalone project on the scale of the whole rest
of this suite (a working two-legged WebRTC relay), not a small addition, and was
deferred by explicit decision rather than time pressure. SAS *equality* -- the
common-path guarantee the whole mechanism exists to give users -- is already covered.

## A note on this environment specifically

Chasing a run of flaky failures during Milestone 9 turned up two separate causes,
worth recording since only one of them was an actual bug:

1. **A real test-suite bug**: every scenario opens two "devices" via
   `browser.newPage()`/`browser.newContext()` directly, which Playwright's own `page`
   fixture doesn't track — a worker reuses one browser process across every test in a
   run, so untracked pages leaked their WebRTC resources (open RTCPeerConnections,
   UDP ports, a live signaling WebSocket) across tests. Fixed with `newPage()` /
   `newPeerContext()` fixtures in `fixtures.ts` that close what they open.
2. **Not a bug**: the same long-lived dev compose stack, after many hours of heavy
   manual iteration in one sitting, degraded enough on its own that even a fresh
   browser process couldn't reliably connect. A plain `docker compose down && up`
   resolved it completely (two clean full-suite runs afterward, each under 17s). This
   is specific to hammering one never-restarted dev instance all day, not something a
   CI run (fresh stack every time) or a real deployment would hit under any usage
   pattern this project expects. Worth a restart if this suite gets flaky again after
   a long local session, before assuming a regression.
