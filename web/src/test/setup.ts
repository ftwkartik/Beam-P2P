import { webcrypto } from "node:crypto";

import "@testing-library/jest-dom/vitest";
// jsdom has no IndexedDB implementation at all; this polyfills `indexedDB` globally
// so transfer/resume-store.ts's IndexedDbResumeStore is tested against a real (if
// in-memory) IndexedDB rather than a hand-written fake of our own store's shape.
import "fake-indexeddb/auto";

// jsdom's `crypto` has getRandomValues but not `.subtle` (as of jsdom 30), which
// engine/sas.ts needs for SHA-256. Real browsers (and Vite's own dev/build output)
// always have it in a secure context, so this polyfill exists purely for tests.
if (globalThis.crypto.subtle === undefined) {
  Object.defineProperty(globalThis, "crypto", { value: webcrypto, configurable: true });
}
