/**
 * A real sender <-> receiver round trip, wired directly to each other rather than to
 * hand-crafted per-side fakes (sender.test.ts and receiver.test.ts each test one side
 * in isolation) -- this is what actually proves the two interoperate, at close to
 * realistic size and the real default block/frame sizes. No browser is available in
 * this environment (docs/roadmap.md Milestone 8's manual-verification note), so this
 * is the closest automated substitute for the "large transfer, bounded memory"
 * exit criterion: MemoryStorage still allocates one buffer per file, but nothing here
 * buffers the *whole transfer* at once, and multi-MB/multi-file correctness is real.
 */
import { describe, expect, it, vi } from "vitest";

import type { ControlChannelLike, DataChannelLike } from "./channels";
import { hashBytesHex } from "./hashing";
import { TransferReceiver, type FileReadyResult } from "./receiver";
import { TransferSender } from "./sender";
import { MemoryStorage } from "../storage/memory-storage";

function randomFile(name: string, size: number, seed: number): File {
  const bytes = new Uint8Array(size);
  let state = seed;
  for (let i = 0; i < size; i++) {
    // A tiny xorshift PRNG: deterministic (reproducible test failures) and fast
    // enough for multi-MB fixtures, unlike Math.random() reseeded per byte.
    state ^= state << 13;
    state ^= state >>> 17;
    state ^= state << 5;
    bytes[i] = state & 0xff;
  }
  return new File([bytes], name);
}

function link(): {
  senderControl: ControlChannelLike;
  senderData: DataChannelLike;
  receiverControl: ControlChannelLike;
  setSender: (s: TransferSender) => void;
  setReceiver: (r: TransferReceiver) => void;
} {
  let sender: TransferSender | undefined;
  let receiver: TransferReceiver | undefined;

  const senderControl: ControlChannelLike = {
    send: (data) => receiver?.handleControlMessage(JSON.parse(data)),
  };
  const receiverControl: ControlChannelLike = {
    send: (data) => sender?.handleControlMessage(JSON.parse(data)),
  };
  const senderData: DataChannelLike = {
    send: (bytes) => receiver?.handleDataFrame(bytes),
    bufferedAmount: 0,
    addEventListener: () => {},
    removeEventListener: () => {},
  };

  return {
    senderControl,
    senderData,
    receiverControl,
    setSender: (s) => (sender = s),
    setReceiver: (r) => (receiver = r),
  };
}

describe("sender <-> receiver end-to-end", () => {
  it("transfers two multi-block files intact at the real default block/frame sizes", async () => {
    const fileA = randomFile("a.bin", 3 * 1024 * 1024 + 12345, 0x1234); // spans several 1 MiB blocks
    const fileB = randomFile("b.bin", 500 * 1024, 0x5678); // under one block

    const wiring = link();
    const readyFiles: FileReadyResult[] = [];

    const receiver = new TransferReceiver({
      storage: new MemoryStorage(),
      control: wiring.receiverControl,
      onOffer: () => receiver.accept(),
      onFileReady: (result) => readyFiles.push(result),
    });
    wiring.setReceiver(receiver);

    const sender = new TransferSender({
      transferId: "integration-1",
      files: [fileA, fileB],
      control: wiring.senderControl,
      data: wiring.senderData,
    });
    wiring.setSender(sender);

    sender.start();
    await vi.waitUntil(() => sender.getPhase() === "completed" && readyFiles.length === 2, { timeout: 5000 });

    expect(sender.getPhase()).toBe("completed");
    expect(receiver.getPhase()).toBe("completed");

    const resultA = readyFiles.find((f) => f.offer.path === "a.bin")!;
    const resultB = readyFiles.find((f) => f.offer.path === "b.bin")!;

    const [expectedHashA, actualHashA] = await Promise.all([
      hashBytesHex(new Uint8Array(await fileA.arrayBuffer())),
      hashBytesHex(new Uint8Array(await resultA.blob.arrayBuffer())),
    ]);
    expect(actualHashA).toBe(expectedHashA);

    const [expectedHashB, actualHashB] = await Promise.all([
      hashBytesHex(new Uint8Array(await fileB.arrayBuffer())),
      hashBytesHex(new Uint8Array(await resultB.blob.arrayBuffer())),
    ]);
    expect(actualHashB).toBe(expectedHashB);
  });
});
