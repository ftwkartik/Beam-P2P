import { webcrypto } from "node:crypto";

import "@testing-library/jest-dom/vitest";

// jsdom's `crypto` has getRandomValues but not `.subtle` (as of jsdom 30), which
// engine/sas.ts needs for SHA-256. Real browsers (and Vite's own dev/build output)
// always have it in a secure context, so this polyfill exists purely for tests.
if (globalThis.crypto.subtle === undefined) {
  Object.defineProperty(globalThis, "crypto", { value: webcrypto, configurable: true });
}
