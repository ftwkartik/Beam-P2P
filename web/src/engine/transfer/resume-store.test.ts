import { beforeEach, describe, expect, it } from "vitest";

import type { ResumeStore } from "./resume-store";
import { IndexedDbResumeStore, NullResumeStore } from "./resume-store";

describe("NullResumeStore", () => {
  it("never claims a transfer was accepted or has a saved file", async () => {
    const store: ResumeStore = new NullResumeStore();
    await store.markAccepted("t1");
    await store.saveFile("t1", 0, { size: 10, bitmapBase64: "gA==", blockHashesHex: [null] });

    expect(await store.wasAccepted("t1")).toBe(false);
    expect(await store.loadFile("t1", 0)).toBeNull();
  });
});

describe("IndexedDbResumeStore", () => {
  // fake-indexeddb's in-memory database is shared per module instance, not reset
  // between tests automatically -- give every test its own transfer id namespace
  // instead of trying to tear the database down.
  let store: IndexedDbResumeStore;

  beforeEach(() => {
    store = new IndexedDbResumeStore();
  });

  it("has nothing accepted or saved for an unknown transfer", async () => {
    expect(await store.wasAccepted("unknown-1")).toBe(false);
    expect(await store.loadFile("unknown-1", 0)).toBeNull();
  });

  it("remembers a transfer was accepted", async () => {
    await store.markAccepted("accept-1");
    expect(await store.wasAccepted("accept-1")).toBe(true);
    expect(await store.wasAccepted("accept-2")).toBe(false);
  });

  it("round-trips a saved file's bitmap and block hashes", async () => {
    const state = {
      size: 3 * 1024 * 1024,
      bitmapBase64: "gEA=",
      blockHashesHex: ["aa".repeat(32), null, "bb".repeat(32)], // pragma: allowlist secret
    };
    await store.saveFile("file-1", 2, state);

    expect(await store.loadFile("file-1", 2)).toEqual(state);
    expect(await store.loadFile("file-1", 0)).toBeNull(); // a different file index in the same transfer
  });

  it("overwrites a previous save for the same transfer and file index", async () => {
    await store.saveFile("overwrite-1", 0, { size: 10, bitmapBase64: "gA==", blockHashesHex: [null] });
    await store.saveFile("overwrite-1", 0, { size: 10, bitmapBase64: "wA==", blockHashesHex: ["cc".repeat(32)] }); // pragma: allowlist secret

    const loaded = await store.loadFile("overwrite-1", 0);
    expect(loaded?.bitmapBase64).toBe("wA==");
  });

  it("clears the accepted flag and every file record for a transfer, leaving others intact", async () => {
    await store.markAccepted("clear-1");
    await store.saveFile("clear-1", 0, { size: 10, bitmapBase64: "gA==", blockHashesHex: [null] });
    await store.saveFile("clear-1", 1, { size: 20, bitmapBase64: "gA==", blockHashesHex: [null] });
    await store.markAccepted("clear-2");
    await store.saveFile("clear-2", 0, { size: 30, bitmapBase64: "gA==", blockHashesHex: [null] });

    await store.clearTransfer("clear-1");

    expect(await store.wasAccepted("clear-1")).toBe(false);
    expect(await store.loadFile("clear-1", 0)).toBeNull();
    expect(await store.loadFile("clear-1", 1)).toBeNull();
    // A different transfer's records must survive -- clearTransfer must not clear
    // everything just because a file-index prefix match is loose string matching.
    expect(await store.wasAccepted("clear-2")).toBe(true);
    expect(await store.loadFile("clear-2", 0)).not.toBeNull();
  });

  it("does not let one transfer's file-index prefix collide with another's", async () => {
    // "t-1:1" (transfer "t-1", file 1) vs "t-1:10" (transfer "t-1", file 10) is a
    // real same-transfer case, not a collision -- but "t-1" vs "t-10" must not clear
    // each other's records via a naive `startsWith` on the transfer id alone.
    await store.saveFile("t-1", 0, { size: 10, bitmapBase64: "gA==", blockHashesHex: [null] });
    await store.saveFile("t-10", 0, { size: 10, bitmapBase64: "gA==", blockHashesHex: [null] });

    await store.clearTransfer("t-1");

    expect(await store.loadFile("t-1", 0)).toBeNull();
    expect(await store.loadFile("t-10", 0)).not.toBeNull();
  });
});
