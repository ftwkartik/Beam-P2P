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

import { BlockBitmap } from "./bitmap";
import type { ControlChannelLike, DataChannelLike } from "./channels";
import { hashBytesHex } from "./hashing";
import { TransferReceiver, type FileReadyResult } from "./receiver";
import { IndexedDbResumeStore } from "./resume-store";
import { TransferSender } from "./sender";
import { MemoryStorage } from "../storage/memory-storage";
import type { FileStorageHandle, TransferStorage } from "../storage/types";

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

/** Wraps a data channel so only the first `frameLimit` frames actually get through --
 * simulating a connection dropping partway through a transfer deterministically
 * (by frame count, not by timing), since nothing here has real network latency to
 * race against. */
function capFrames(data: DataChannelLike, frameLimit: number): DataChannelLike {
  let delivered = 0;
  return {
    ...data,
    send: (bytes) => {
      if (delivered >= frameLimit) return;
      delivered++;
      data.send(bytes);
    },
  };
}

/** A `durable: true` storage test double backed by a byte map that outlives any one
 * storage instance -- reopening a file from a brand-new `DurableStorage` (as
 * session.ts does with a fresh `OpfsStorage(transferId)` after every reconnect)
 * finds the same bytes, unlike plain `MemoryStorage` (see its `durable: false`). */
class DurableBytes {
  private readonly filesByIndex = new Map<number, Uint8Array>();

  get(fileIndex: number, size: number): Uint8Array {
    let bytes = this.filesByIndex.get(fileIndex);
    if (!bytes) {
      bytes = new Uint8Array(size);
      this.filesByIndex.set(fileIndex, bytes);
    }
    return bytes;
  }
}

class DurableFileHandle implements FileStorageHandle {
  readonly fileIndex: number;
  readonly size: number;
  private readonly bytes: Uint8Array;

  constructor(fileIndex: number, size: number, bytes: Uint8Array) {
    this.fileIndex = fileIndex;
    this.size = size;
    this.bytes = bytes;
  }

  async writeAt(offset: number, data: Uint8Array): Promise<void> {
    this.bytes.set(data, offset);
  }

  async readRange(start: number, end: number): Promise<Uint8Array> {
    return this.bytes.slice(start, end);
  }

  async finalize(): Promise<Blob> {
    return new Blob([this.bytes.buffer as ArrayBuffer]);
  }

  async close(): Promise<void> {
    // no-op
  }
}

class DurableStorage implements TransferStorage {
  readonly durable = true;
  private readonly persisted: DurableBytes;

  constructor(persisted: DurableBytes) {
    this.persisted = persisted;
  }

  async openFile(fileIndex: number, size: number): Promise<FileStorageHandle> {
    return new DurableFileHandle(fileIndex, size, this.persisted.get(fileIndex, size));
  }

  async deleteFile(): Promise<void> {
    // no-op
  }

  async deleteAll(): Promise<void> {
    // no-op
  }
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

  it("resumes after a mid-transfer drop, sending only the blocks the receiver never verified", async () => {
    const blockSize = 64 * 1024;
    const blockCount = 20;
    const framesBeforeDrop = 10; // one frame per block here (blockSize === frameSize)
    const file = randomFile("resume.bin", blockCount * blockSize, 0x9abc);
    const transferId = "resume-1";

    const persistedBytes = new DurableBytes();
    const resumeStore = new IndexedDbResumeStore();

    // --- first connection: only the first `framesBeforeDrop` blocks ever arrive ---
    const link1 = link();
    let bytesVerified1 = 0;
    const receiver1 = new TransferReceiver({
      storage: new DurableStorage(persistedBytes),
      control: link1.receiverControl,
      resumeStore,
      onOffer: () => receiver1.accept(),
      onProgress: (progress) => {
        bytesVerified1 = progress.totalBytesVerified;
      },
    });
    link1.setReceiver(receiver1);

    const sender1 = new TransferSender({
      transferId,
      files: [file],
      control: link1.senderControl,
      data: capFrames(link1.senderData, framesBeforeDrop),
      blockSize,
      frameSize: blockSize,
    });
    link1.setSender(sender1);

    sender1.start();
    await vi.waitUntil(() => sender1.getPhase() === "completed", { timeout: 5000 });
    // The sender itself doesn't know delivery was capped (it never waits for acks),
    // so give the receiver's own (real) processing of whatever did arrive a moment
    // to settle before reading its progress.
    await vi.waitUntil(() => bytesVerified1 >= framesBeforeDrop * blockSize, { timeout: 5000 });

    const bytesVerifiedBeforeDrop = bytesVerified1;
    expect(bytesVerifiedBeforeDrop).toBe(framesBeforeDrop * blockSize);
    expect(bytesVerifiedBeforeDrop).toBeLessThan(file.size); // a real partial transfer, not the whole file

    // The persisted bitmap only ever holds whole ack batches (docs/protocol.md
    // §4.1's 8-block/250ms cadence) -- it can lag behind what's actually been
    // verified in memory by up to one partial batch, a deliberate cost trade-off
    // (writing to IndexedDB every single block would be needlessly expensive for a
    // multi-GB transfer). Read back exactly what was persisted rather than assuming
    // it matches `bytesVerifiedBeforeDrop`.
    const persisted = await resumeStore.loadFile(transferId, 0);
    const persistedBlockCount = persisted ? BlockBitmap.fromBase64(persisted.bitmapBase64, blockCount).count() : 0;
    expect(persistedBlockCount).toBeGreaterThan(0);
    expect(persistedBlockCount).toBeLessThanOrEqual(framesBeforeDrop);

    // --- second connection: a brand-new sender and receiver, same transfer id ---
    const link2 = link();
    let bytesSentAfterResume = 0;
    const readyFiles: FileReadyResult[] = [];
    const receiver2 = new TransferReceiver({
      storage: new DurableStorage(persistedBytes),
      control: link2.receiverControl,
      resumeStore,
      onFileReady: (result) => readyFiles.push(result),
    });
    link2.setReceiver(receiver2);

    const sender2 = new TransferSender({
      transferId,
      files: [file],
      control: link2.senderControl,
      data: link2.senderData,
      blockSize,
      onProgress: (progress) => {
        bytesSentAfterResume = progress.totalBytesSent;
      },
    });
    link2.setSender(sender2);

    sender2.start();
    await vi.waitUntil(() => sender2.getPhase() === "completed" && readyFiles.length === 1, { timeout: 5000 });

    expect(receiver2.getPhase()).toBe("completed");
    // No consent prompt the second time -- accept() ran automatically because this
    // transfer id was already accepted on the first connection.
    expect(receiver2.getPhase()).not.toBe("offered");

    const [expectedHash, actualHash] = await Promise.all([
      hashBytesHex(new Uint8Array(await file.arrayBuffer())),
      hashBytesHex(new Uint8Array(await readyFiles[0].blob.arrayBuffer())),
    ]);
    expect(actualHash).toBe(expectedHash);

    // The resumed connection sent exactly the blocks the persisted bitmap didn't
    // already have -- proving the sender genuinely skipped them, not just that the
    // transfer happened to complete.
    expect(bytesSentAfterResume).toBe(file.size - persistedBlockCount * blockSize);
  });
});
