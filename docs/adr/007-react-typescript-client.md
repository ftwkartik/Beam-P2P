# ADR-007: React + TypeScript web client with a framework-free transfer engine

- **Status:** Accepted
- **Date:** 2026-08-06

## Context
Peer connection management, transfer state machines, workers and storage make the client substantial. It has to be
testable without a browser UI and it must share the protocol contract with Python.

## Decision
Use **Vite + React + TypeScript + Tailwind**. All WebRTC, protocol, hashing and storage logic lives in
`web/src/engine/` as **framework-free TypeScript modules** with explicit state machines. React subscribes via small
**Zustand** stores. Protocol types are **generated** from the Pydantic JSON Schema. Tests use Vitest (engine) and Playwright (E2E).

## Alternatives
- **Server-rendered Jinja + HTMX:** HTMX adds little to a real-time peer-to-peer page. The heavy logic is client-side regardless.
- **Svelte / Vue:** fine choices. React matches the reference project and is the most widely recognized.
- **Redux Toolkit:** heavier than needed for a few stores.

## Trade-offs
- A Node toolchain in the repo (for build and tests only). The runtime image is nginx serving static files.
- Type generation adds a build step, and CI fails on drift.

## Consequences
- The engine can be unit-tested with fake channels, and the UI stays thin.
- The same engine could later power a PWA or an extension.
