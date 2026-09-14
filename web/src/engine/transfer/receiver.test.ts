import { beforeEach, describe, expect, it, vi } from "vitest";

import type { PeerMessage } from "../../protocol/generated/peer-message";
import { BlockBitmap } from "./bitmap";
import type { ControlChannelLike } from "./channels";
import { createFrame, packFrame } from "./framing";
import { deriveFileRootHash, hashBytes, hashBytesHex } from "./hashing";
import { TransferReceiver } from "./receiver";
import { IndexedDbResumeStore } from "./resume-store";
import type { ResumeStore } from "./resume-store";
import { MemoryStorage } from "../storage/memory-storage";
import type { FileStorageHandle, TransferStorage } from "../storage/types";

/** A `durable: true` storage test double whose bytes live outside any one storage
 * instance -- reopening a file (even from a brand-new `DurableMemoryStorage`, as
 * session.ts does with a fresh `OpfsStorage(transferId)` after every reconnect)
 * finds the same bytes. This is what makes it a faithful stand-in for OPFS's
 * disk-backed persistence, unlike plain `MemoryStorage` (whose `openFile` always
 * hands back a fresh zeroed buffer, and whose `durable` flag is correctly `false`). */
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

class DurableMemoryStorage implements TransferStorage {
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

function fakeResumeStore(): ResumeStore {
  return {
    wasAccepted: vi.fn(async () => false),
    markAccepted: vi.fn(async () => {}),
    loadFile: vi.fn(async () => null),
    saveFile: vi.fn(async () => {}),
    clearTransfer: vi.fn(async () => {}),
  };
}

class FakeControlChannel implements ControlChannelLike {
  sent: PeerMessage[] = [];
  send(data: string): void {
    this.sent.push(JSON.parse(data) as PeerMessage);
  }
}

function offerFiles(files: { index: number; path: string; size: number }[], blockSize = 4): PeerMessage {
  return {
    type: "offer_files",
    transfer_id: "t1",
    block_size: blockSize,
    files: files.map((f) => ({ ...f, mime: "text/plain", mtime: null })),
  };
}

function build() {
  const control = new FakeControlChannel();
  const storage = new MemoryStorage();
  const onOffer = vi.fn();
  const onPhaseChange = vi.fn();
  const onProgress = vi.fn();
  const onFileReady = vi.fn();
  const onError = vi.fn();
  const receiver = new TransferReceiver({
    storage,
    control,
    onOffer,
    onPhaseChange,
    onProgress,
    onFileReady,
    onError,
  });
  return { receiver, control, storage, onOffer, onPhaseChange, onProgress, onFileReady, onError };
}

/** Plays the sender's side of a transfer directly against a receiver, block by block,
 * with full control over each block's declared hash and frame size -- so tests can
 * both exercise the happy path and deliberately corrupt a block. */
async function sendFile(
  receiver: TransferReceiver,
  fileIndex: number,
  bytes: Uint8Array,
  blockSize: number,
  frameSize: number,
  corruptBlockIndex: number | null = null,
): Promise<string> {
  const blockCount = bytes.length === 0 ? 0 : Math.ceil(bytes.length / blockSize);
  const blockHashes: Uint8Array[] = [];

  for (let b = 0; b < blockCount; b++) {
    const start = b * blockSize;
    const end = Math.min(start + blockSize, bytes.length);
    const blockBytes = bytes.slice(start, end);
    const hashHex = await hashBytesHex(blockBytes);
    blockHashes.push(await hashBytes(blockBytes));

    receiver.handleControlMessage({ type: "block", file: fileIndex, index: b, sha256: hashHex });

    const sendBytes = b === corruptBlockIndex ? flipFirstByte(blockBytes) : blockBytes;
    for (let offset = 0; offset < sendBytes.length; offset += frameSize) {
      const chunk = sendBytes.subarray(offset, Math.min(offset + frameSize, sendBytes.length));
      const isLast = offset + chunk.length >= sendBytes.length;
      const frame = createFrame(fileIndex, BigInt(start + offset), chunk, isLast);
      receiver.handleDataFrame(packFrame(frame));
    }
    // Let the async frame-processing microtasks (write + verify) settle before the
    // next block's control message, mirroring a real sender waiting for nothing.
    await flushMicrotasks();
  }

  return deriveFileRootHash(BigInt(bytes.length), blockHashes);
}

function flipFirstByte(bytes: Uint8Array): Uint8Array {
  const copy = bytes.slice();
  copy[0] = copy[0] ^ 0xff;
  return copy;
}

async function flushMicrotasks(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 0));
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("offer / accept / decline", () => {
  it("surfaces the offer via onOffer and moves to 'offered'", async () => {
    const { receiver, onOffer } = build();
    receiver.handleControlMessage(offerFiles([{ index: 0, path: "a.txt", size: 10 }]));
    await flushMicrotasks();

    expect(receiver.getPhase()).toBe("offered");
    expect(onOffer).toHaveBeenCalledWith({
      transferId: "t1",
      blockSize: 4,
      files: [expect.objectContaining({ index: 0, path: "a.txt", size: 10 })],
    });
  });

  it("accept() sends an accept message and moves to 'receiving'", () => {
    const { receiver, control } = build();
    receiver.handleControlMessage(offerFiles([{ index: 0, path: "a.txt", size: 10 }]));
    receiver.accept();

    expect(receiver.getPhase()).toBe("receiving");
    expect(control.sent).toContainEqual({ type: "accept", transfer_id: "t1", have: {} });
  });

  it("decline() sends a decline message with the reason and moves to 'declined'", () => {
    const { receiver, control } = build();
    receiver.handleControlMessage(offerFiles([{ index: 0, path: "a.txt", size: 10 }]));
    receiver.decline("not interested");

    expect(receiver.getPhase()).toBe("declined");
    expect(control.sent).toContainEqual({ type: "decline", transfer_id: "t1", reason: "not interested" });
  });
});

describe("happy path", () => {
  it("verifies every block, the file, and reaches 'completed'", async () => {
    const { receiver, control, onFileReady, onPhaseChange } = build();
    const content = new TextEncoder().encode("0123456789"); // 10 bytes, blockSize=4 -> 3 blocks
    receiver.handleControlMessage(offerFiles([{ index: 0, path: "a.txt", size: content.length }]));
    receiver.accept();

    const expectedRoot = await sendFile(receiver, 0, content, 4, 2);
    receiver.handleControlMessage({ type: "file_done", file: 0, sha256: expectedRoot });
    await flushMicrotasks();

    expect(control.sent).toContainEqual({ type: "file_verified", file: 0, ok: true });
    expect(onFileReady).toHaveBeenCalledOnce();
    const [{ blob, fileIndex }] = onFileReady.mock.calls[0];
    expect(fileIndex).toBe(0);
    expect(new TextDecoder().decode(await blob.arrayBuffer())).toBe("0123456789");
    expect(onPhaseChange).toHaveBeenCalledWith("completed");
  });

  it("reconstructs a block correctly even when its frames arrive out of order", async () => {
    const { receiver, control } = build();
    const content = new TextEncoder().encode("abcd"); // 1 block, 2 frames at frameSize=2
    receiver.handleControlMessage(offerFiles([{ index: 0, path: "a.txt", size: content.length }]));
    receiver.accept();

    const hashHex = await hashBytesHex(content);
    receiver.handleControlMessage({ type: "block", file: 0, index: 0, sha256: hashHex });
    // Send the second frame before the first.
    const frame2 = createFrame(0, 2n, content.subarray(2, 4), true);
    const frame1 = createFrame(0, 0n, content.subarray(0, 2), false);
    receiver.handleDataFrame(packFrame(frame2));
    receiver.handleDataFrame(packFrame(frame1));
    await flushMicrotasks();

    const rootHash = await deriveFileRootHash(4n, [await hashBytes(content)]);
    receiver.handleControlMessage({ type: "file_done", file: 0, sha256: rootHash });
    await flushMicrotasks();

    expect(control.sent).toContainEqual({ type: "file_verified", file: 0, ok: true });
  });

  it("handles a zero-byte file with no blocks", async () => {
    const { receiver, control, onFileReady } = build();
    receiver.handleControlMessage(offerFiles([{ index: 0, path: "empty.txt", size: 0 }]));
    receiver.accept();

    const rootHash = await deriveFileRootHash(0n, []);
    receiver.handleControlMessage({ type: "file_done", file: 0, sha256: rootHash });
    await flushMicrotasks();

    expect(control.sent).toContainEqual({ type: "file_verified", file: 0, ok: true });
    expect(onFileReady).toHaveBeenCalledOnce();
  });

  it("completes only once every file in the manifest is verified", async () => {
    const { receiver, onPhaseChange } = build();
    const a = new TextEncoder().encode("ab");
    const b = new TextEncoder().encode("cd");
    receiver.handleControlMessage(
      offerFiles([
        { index: 0, path: "a.txt", size: a.length },
        { index: 1, path: "b.txt", size: b.length },
      ]),
    );
    receiver.accept();

    const rootA = await sendFile(receiver, 0, a, 4, 4);
    receiver.handleControlMessage({ type: "file_done", file: 0, sha256: rootA });
    await flushMicrotasks();
    expect(onPhaseChange).not.toHaveBeenCalledWith("completed");

    const rootB = await sendFile(receiver, 1, b, 4, 4);
    receiver.handleControlMessage({ type: "file_done", file: 1, sha256: rootB });
    await flushMicrotasks();
    expect(onPhaseChange).toHaveBeenCalledWith("completed");
  });
});

describe("hash mismatch handling", () => {
  it("nacks a corrupted block instead of accepting it", async () => {
    const { receiver, control } = build();
    const content = new TextEncoder().encode("0123456789");
    receiver.handleControlMessage(offerFiles([{ index: 0, path: "a.txt", size: content.length }]));
    receiver.accept();

    await sendFile(receiver, 0, content, 4, 2, 1); // corrupt block 1

    expect(control.sent).toContainEqual({ type: "nack", file: 0, index: 1, reason: "hash_mismatch" });
    expect(control.sent.some((m) => m.type === "file_verified")).toBe(false);
  });

  it("accepts a corrected block resent after a nack", async () => {
    const { receiver, control } = build();
    const content = new TextEncoder().encode("0123456789");
    receiver.handleControlMessage(offerFiles([{ index: 0, path: "a.txt", size: content.length }]));
    receiver.accept();

    const expectedRoot = await sendFile(receiver, 0, content, 4, 2, 1); // corrupt block 1
    expect(control.sent).toContainEqual({ type: "nack", file: 0, index: 1, reason: "hash_mismatch" });

    // Resend just the corrected block (as the real sender would on nack).
    const start = 1 * 4;
    const end = Math.min(start + 4, content.length);
    const blockBytes = content.slice(start, end);
    const hashHex = await hashBytesHex(blockBytes);
    receiver.handleControlMessage({ type: "block", file: 0, index: 1, sha256: hashHex });
    for (let offset = 0; offset < blockBytes.length; offset += 2) {
      const chunk = blockBytes.subarray(offset, Math.min(offset + 2, blockBytes.length));
      const isLast = offset + chunk.length >= blockBytes.length;
      receiver.handleDataFrame(packFrame(createFrame(0, BigInt(start + offset), chunk, isLast)));
    }
    await flushMicrotasks();

    receiver.handleControlMessage({ type: "file_done", file: 0, sha256: expectedRoot });
    await flushMicrotasks();

    expect(control.sent).toContainEqual({ type: "file_verified", file: 0, ok: true });
  });

  it("fails the transfer when the file root hash doesn't match", async () => {
    const { receiver, control, onError } = build();
    const content = new TextEncoder().encode("0123456789");
    receiver.handleControlMessage(offerFiles([{ index: 0, path: "a.txt", size: content.length }]));
    receiver.accept();

    await sendFile(receiver, 0, content, 4, 2);
    receiver.handleControlMessage({ type: "file_done", file: 0, sha256: "0".repeat(64) });
    await flushMicrotasks();

    expect(control.sent).toContainEqual({ type: "file_verified", file: 0, ok: false });
    expect(receiver.getPhase()).toBe("failed");
    expect(onError).toHaveBeenCalled();
  });
});

describe("ack batching", () => {
  it("flushes an ack immediately once 8 blocks are verified", async () => {
    const { receiver, control } = build();
    const content = new Uint8Array(32); // blockSize=1 -> 32 blocks, well over the batch size
    receiver.handleControlMessage(offerFiles([{ index: 0, path: "a.bin", size: content.length }], 1));
    receiver.accept();

    await sendFile(receiver, 0, content, 1, 1);

    const acks = control.sent.filter((m) => m.type === "ack");
    expect(acks.length).toBeGreaterThan(0);
    const totalAcked = acks.reduce((sum, m) => sum + (m as { verified: { start: number; end: number }[] }).verified
      .reduce((n, r) => n + (r.end - r.start + 1), 0), 0);
    expect(totalAcked).toBe(32);
  });

  it("flushes a partial batch after the timeout", async () => {
    vi.useFakeTimers();
    const { receiver, control } = build();
    const content = new Uint8Array(8); // blockSize=1 -> 8 blocks total, but corrupt none; send only 2
    receiver.handleControlMessage(offerFiles([{ index: 0, path: "a.bin", size: content.length }], 1));
    receiver.accept();

    for (let b = 0; b < 2; b++) {
      const blockBytes = content.subarray(b, b + 1);
      const hashHex = await hashBytesHex(blockBytes);
      receiver.handleControlMessage({ type: "block", file: 0, index: b, sha256: hashHex });
      receiver.handleDataFrame(packFrame(createFrame(0, BigInt(b), blockBytes, true)));
      await Promise.resolve();
    }

    expect(control.sent.some((m) => m.type === "ack")).toBe(false);
    await vi.advanceTimersByTimeAsync(250);
    expect(control.sent).toContainEqual({ type: "ack", file: 0, verified: [{ start: 0, end: 1 }] });

    vi.useRealTimers();
  });
});

describe("cancel", () => {
  it("moves to 'cancelled' on a cancel message", () => {
    const { receiver } = build();
    receiver.handleControlMessage(offerFiles([{ index: 0, path: "a.txt", size: 4 }]));
    receiver.accept();
    receiver.handleControlMessage({ type: "cancel", transfer_id: "t1", reason: "changed my mind" });
    expect(receiver.getPhase()).toBe("cancelled");
  });
});

describe("resume", () => {
  it("never touches the resume store when storage isn't durable", async () => {
    const control = new FakeControlChannel();
    const resumeStore = fakeResumeStore();
    const receiver = new TransferReceiver({ storage: new MemoryStorage(), control, resumeStore });

    receiver.handleControlMessage(offerFiles([{ index: 0, path: "a.txt", size: 4 }]));
    await flushMicrotasks();
    await receiver.accept();
    receiver.handleControlMessage({ type: "cancel", transfer_id: "t1", reason: "done" });

    expect(resumeStore.wasAccepted).not.toHaveBeenCalled();
    expect(resumeStore.markAccepted).not.toHaveBeenCalled();
    expect(resumeStore.saveFile).not.toHaveBeenCalled();
    expect(resumeStore.clearTransfer).not.toHaveBeenCalled();
    expect(control.sent).toContainEqual({ type: "accept", transfer_id: "t1", have: {} });
  });

  it("resumes from a persisted bitmap after a simulated reconnect, sending only the missing blocks", async () => {
    const storage = new DurableMemoryStorage(new DurableBytes());
    const resumeStore = new IndexedDbResumeStore();
    const bytes = new Uint8Array(40);
    for (let i = 0; i < bytes.length; i++) bytes[i] = i;
    const blockSize = 4;
    const blockCount = 10;
    const sentBeforeDrop = 8; // exactly one ack batch (ACK_BATCH_SIZE), so it flushes -- and persists -- synchronously

    // --- first connection: 8 of 10 blocks arrive, then it drops ---
    const control1 = new FakeControlChannel();
    const receiver1 = new TransferReceiver({ storage, control: control1, resumeStore });
    receiver1.handleControlMessage(offerFiles([{ index: 0, path: "a.bin", size: bytes.length }], blockSize));
    await flushMicrotasks();
    await receiver1.accept();

    for (let b = 0; b < sentBeforeDrop; b++) {
      const start = b * blockSize;
      const chunk = bytes.slice(start, start + blockSize);
      const hashHex = await hashBytesHex(chunk);
      receiver1.handleControlMessage({ type: "block", file: 0, index: b, sha256: hashHex });
      receiver1.handleDataFrame(packFrame(createFrame(0, BigInt(start), chunk, true)));
      await flushMicrotasks();
    }
    // The ack (and the persisted bitmap behind it) batches every 8 blocks or 250 ms
    // (docs/protocol.md §4.1); a real wait past that window makes this deterministic
    // regardless of exactly how many microtask hops hashing took above.
    await new Promise((resolve) => setTimeout(resolve, 300));
    expect(control1.sent.some((m) => m.type === "ack")).toBe(true);

    // --- reconnect: a brand new receiver object, same durable storage + resume store ---
    const control2 = new FakeControlChannel();
    const onOffer2 = vi.fn();
    const onFileReady2 = vi.fn();
    const receiver2 = new TransferReceiver({
      storage,
      control: control2,
      resumeStore,
      onOffer: onOffer2,
      onFileReady: onFileReady2,
    });
    receiver2.handleControlMessage(offerFiles([{ index: 0, path: "a.bin", size: bytes.length }], blockSize));
    // The auto-accept path round-trips through the (fake) IndexedDB for every file's
    // saved bitmap, which schedules its own tasks beyond a single microtask flush.
    await vi.waitUntil(() => control2.sent.some((m) => m.type === "accept"), { timeout: 1000 });

    // Already accepted before the drop: resumes silently, no consent prompt again.
    expect(onOffer2).not.toHaveBeenCalled();
    expect(receiver2.getPhase()).toBe("receiving");
    const acceptMessage = control2.sent.find((m) => m.type === "accept");
    expect(acceptMessage).toMatchObject({ type: "accept", transfer_id: "t1" });
    const have = (acceptMessage as { have: Record<string, string> }).have;
    expect(Object.keys(have)).toEqual(["0"]);
    const resumedBitmap = BlockBitmap.fromBase64(have[0], blockCount);
    const resumedCount = resumedBitmap.count();
    // At least a whole ack batch's worth resumed, and strictly fewer than all of
    // them -- proving this is a genuine partial resume, not "everything" or "nothing".
    expect(resumedCount).toBeGreaterThanOrEqual(8);
    expect(resumedCount).toBeLessThan(blockCount);

    // Only send the blocks the resumed bitmap doesn't already have.
    const allBlockHashes: Uint8Array[] = [];
    for (let b = 0; b < blockCount; b++) {
      const start = b * blockSize;
      const end = Math.min(start + blockSize, bytes.length);
      const chunk = bytes.slice(start, end);
      allBlockHashes.push(await hashBytes(chunk));
      if (resumedBitmap.has(b)) continue;
      const hashHex = await hashBytesHex(chunk);
      receiver2.handleControlMessage({ type: "block", file: 0, index: b, sha256: hashHex });
      receiver2.handleDataFrame(packFrame(createFrame(0, BigInt(start), chunk, true)));
      await flushMicrotasks();
    }

    const rootHash = await deriveFileRootHash(BigInt(bytes.length), allBlockHashes);
    receiver2.handleControlMessage({ type: "file_done", file: 0, sha256: rootHash });
    await flushMicrotasks();

    expect(receiver2.getPhase()).toBe("completed");
    expect(onFileReady2).toHaveBeenCalledTimes(1);
    const resultBytes = new Uint8Array(await (onFileReady2.mock.calls[0][0] as { blob: Blob }).blob.arrayBuffer());
    expect(Array.from(resultBytes)).toEqual(Array.from(bytes));

    // A completed transfer's resume records are cleaned up.
    expect(await resumeStore.wasAccepted("t1")).toBe(false);
    expect(await resumeStore.loadFile("t1", 0)).toBeNull();
  });

  it("clears resume records when the receiver declines", async () => {
    const storage = new DurableMemoryStorage(new DurableBytes());
    const resumeStore = new IndexedDbResumeStore();
    const control = new FakeControlChannel();
    const receiver = new TransferReceiver({ storage, control, resumeStore });

    receiver.handleControlMessage(offerFiles([{ index: 0, path: "a.txt", size: 4 }]));
    await flushMicrotasks();
    await receiver.accept();
    await flushMicrotasks();
    expect(await resumeStore.wasAccepted("t1")).toBe(true);

    receiver.decline("no thanks");
    expect(await resumeStore.wasAccepted("t1")).toBe(false);
  });
});
