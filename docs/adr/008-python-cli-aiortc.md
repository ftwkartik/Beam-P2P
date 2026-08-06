# ADR-008: A Python CLI peer built on aiortc

- **Status:** Accepted
- **Date:** 2026-08-06

## Context
Sending files from servers, terminals and scripts is a common need (think `magic-wormhole`), and it showcases Python
beyond the web tier. It must interoperate with browsers.

## Decision
Build `beam` with **Typer** + **rich**, using **aiortc** for WebRTC data channels and the shared `beam_protocol`
package. Commands: `beam send PATH...` (prints code, link and QR) and `beam receive CODE [--dir] [--yes] [--overwrite]`.
Storage writes to the filesystem through the path-safety layer, with resume state in `.beam-partial/`.

## Alternatives
- **Browser only:** loses the terminal use case and the strongest Python showcase.
- **Relay-through-server CLI:** simpler networking, but not peer-to-peer, and bytes pass through the server.
- **libdatachannel Python bindings:** faster, but less mature bindings and packaging.

## Trade-offs
- aiortc's pure-Python SCTP limits throughput compared with browsers. This is measured and documented. The value here is interop and automation.
- aiortc depends on native libraries (libsrtp, via PyAV). They ship as wheels on Linux, macOS and Windows.

## Consequences
- The interop test matrix (CLI↔CLI, CLI↔browser) guards the protocol contract.
- The CLI can be packaged with `uv tool install`.
