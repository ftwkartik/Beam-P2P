import { beforeEach, describe, expect, it, vi } from "vitest";

import type { PeerMessage } from "../../protocol/generated/peer-message";
import { BlockBitmap } from "./bitmap";
import { unpackFrame } from "./framing";
import { hashBytesHex } from "./hashing";
import type { ControlChannelLike, DataChannelLike } from "./sender";
import { TransferSender } from "./sender";

class FakeControlChannel implements ControlChannelLike {
  sent: PeerMessage[] = [];
  send(data: string): void {
    this.sent.push(JSON.parse(data) as PeerMessage);
  }
}

class FakeDataChannel implements DataChannelLike {
  sentFrames: Uint8Array[] = [];
  bufferedAmount = 0;
  private listeners: (() => void)[] = [];

  send(data: Uint8Array): void {
    this.sentFrames.push(data);
  }

  addEventListener(_type: "bufferedamountlow", listener: () => void): void {
    this.listeners.push(listener);
  }

  removeEventListener(_type: "bufferedamountlow", listener: () => void): void {
    this.listeners = this.listeners.filter((l) => l !== listener);
  }

  fireBufferedAmountLow(): void {
    for (const listener of [...this.listeners]) listener();
  }
}

function build(files: File[], overrides: Partial<Record<string, unknown>> = {}) {
  const control = new FakeControlChannel();
  const data = new FakeDataChannel();
  const onPhaseChange = vi.fn();
  const onProgress = vi.fn();
  const onError = vi.fn();
  const sender = new TransferSender({
    transferId: "t1",
    files,
    control,
    data,
    blockSize: 4,
    frameSize: 2,
    onPhaseChange,
    onProgress,
    onError,
    ...overrides,
  });
  return { sender, control, data, onPhaseChange, onProgress, onError };
}

function accept(have: Record<string, string> = {}): PeerMessage {
  return { type: "accept", transfer_id: "t1", have };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("TransferSender.start", () => {
  it("sends offer_files with the manifest and block size", () => {
    const file = new File(["hello world"], "a.txt");
    const { sender, control } = build([file]);

    sender.start();

    expect(control.sent).toHaveLength(1);
    expect(control.sent[0]).toMatchObject({
      type: "offer_files",
      transfer_id: "t1",
      block_size: 4,
    });
  });
});

describe("a fresh (non-resumed) transfer", () => {
  it("sends every block, then file_done, then transfer_done", async () => {
    const file = new File(["0123456789"], "a.txt"); // 10 bytes, blockSize=4 -> 3 blocks
    const { sender, control, data } = build([file]);
    sender.start();

    sender.handleControlMessage(accept());
    await vi.waitFor(() => expect(sender.getPhase()).toBe("completed"));

    const blockMessages = control.sent.filter((m) => m.type === "block");
    expect(blockMessages).toHaveLength(3);

    const fileDone = control.sent.find((m) => m.type === "file_done");
    expect(fileDone).toBeDefined();

    const transferDone = control.sent.find((m) => m.type === "transfer_done");
    expect(transferDone).toBeDefined();
    expect(control.sent.at(-1)?.type).toBe("transfer_done");

    // 10 bytes at frameSize=2 -> 5 frames; verify they reassemble to the original bytes.
    expect(data.sentFrames).toHaveLength(5);
    const reassembled = new Uint8Array(10);
    for (const packed of data.sentFrames) {
      const frame = unpackFrame(packed);
      reassembled.set(frame.payload, Number(frame.offset));
    }
    expect(new TextDecoder().decode(reassembled)).toBe("0123456789");
  });

  it("announces each block's real SHA-256 before its frames", async () => {
    const file = new File(["ab"], "a.txt"); // 1 block, exactly one frame
    const { sender, control } = build([file]);
    sender.start();
    sender.handleControlMessage(accept());
    await vi.waitFor(() => expect(sender.getPhase()).toBe("completed"));

    const block = control.sent.find((m) => m.type === "block");
    const expectedHash = await hashBytesHex(new TextEncoder().encode("ab"));
    expect(block).toMatchObject({ file: 0, index: 0, sha256: expectedHash });
  });

  it("marks only the last frame of a block as last_of_block", async () => {
    const file = new File(["abcd"], "a.txt"); // exactly one block, 2 frames at frameSize=2
    const { sender, data } = build([file]);
    sender.start();
    sender.handleControlMessage(accept());
    await vi.waitFor(() => expect(sender.getPhase()).toBe("completed"));

    const frames = data.sentFrames.map(unpackFrame);
    expect(frames.map((f) => f.lastOfBlock)).toEqual([false, true]);
  });

  it("handles a zero-byte file with no blocks or frames, only file_done", async () => {
    const file = new File([], "empty.txt");
    const { sender, control, data } = build([file]);
    sender.start();
    sender.handleControlMessage(accept());
    await vi.waitFor(() => expect(sender.getPhase()).toBe("completed"));

    expect(control.sent.some((m) => m.type === "block")).toBe(false);
    expect(data.sentFrames).toHaveLength(0);
    expect(control.sent.find((m) => m.type === "file_done")).toBeDefined();
  });

  it("reports progress as bytes are sent", async () => {
    const file = new File(["0123456789"], "a.txt");
    const { sender, onProgress } = build([file]);
    sender.start();
    sender.handleControlMessage(accept());
    await vi.waitFor(() => expect(sender.getPhase()).toBe("completed"));

    const last = onProgress.mock.calls.at(-1)?.[0];
    expect(last).toMatchObject({ totalBytesSent: 10, totalBytes: 10 });
  });
});

describe("resume via accept.have", () => {
  it("skips already-verified blocks but still hashes them for the root hash", async () => {
    const file = new File(["0123456789"], "a.txt"); // 3 blocks: [0123][4567][89]
    const { sender, control } = build([file]);
    sender.start();

    const have = new BlockBitmap(3);
    have.set(0); // block 0 already verified by the receiver
    sender.handleControlMessage(accept({ "0": have.toBase64() }));
    await vi.waitFor(() => expect(sender.getPhase()).toBe("completed"));

    const blockMessages = control.sent.filter((m) => m.type === "block");
    expect(blockMessages).toHaveLength(2); // only blocks 1 and 2 were (re)sent
    expect(blockMessages.map((m) => (m as { index: number }).index)).toEqual([1, 2]);
  });

  it("produces the same file_done root hash whether or not a block was skipped", async () => {
    const fullFile = new File(["0123456789"], "a.txt");
    const { sender: senderA, control: controlA } = build([fullFile]);
    senderA.start();
    senderA.handleControlMessage(accept());
    await vi.waitFor(() => expect(senderA.getPhase()).toBe("completed"));

    const { sender: senderB, control: controlB } = build([fullFile]);
    senderB.start();
    const have = new BlockBitmap(3);
    have.set(0);
    senderB.handleControlMessage(accept({ "0": have.toBase64() }));
    await vi.waitFor(() => expect(senderB.getPhase()).toBe("completed"));

    const rootA = controlA.sent.find((m) => m.type === "file_done");
    const rootB = controlB.sent.find((m) => m.type === "file_done");
    expect(rootA).toEqual(rootB);
  });
});

describe("nack handling", () => {
  it("resends the exact block on nack", async () => {
    const file = new File(["0123456789"], "a.txt");
    const { sender, control } = build([file]);
    sender.start();
    sender.handleControlMessage(accept());
    await vi.waitFor(() => expect(sender.getPhase()).toBe("completed"));

    const blocksBefore = control.sent.filter((m) => m.type === "block").length;
    sender.handleControlMessage({ type: "nack", file: 0, index: 1, reason: "hash_mismatch" });
    await vi.waitFor(() => {
      expect(control.sent.filter((m) => m.type === "block").length).toBe(blocksBefore + 1);
    });

    const resent = control.sent.filter((m) => m.type === "block").at(-1);
    expect(resent).toMatchObject({ file: 0, index: 1 });
  });

  it("fails the transfer after 3 retries of the same block", async () => {
    const file = new File(["0123456789"], "a.txt");
    const { sender, onError } = build([file]);
    sender.start();
    sender.handleControlMessage(accept());
    await vi.waitFor(() => expect(sender.getPhase()).toBe("completed"));

    for (let i = 0; i < 4; i++) {
      sender.handleControlMessage({ type: "nack", file: 0, index: 1, reason: "hash_mismatch" });
    }

    await vi.waitFor(() => expect(sender.getPhase()).toBe("failed"));
    expect(onError).toHaveBeenCalledWith(expect.stringContaining("3 retries"));
  });
});

describe("decline / cancel / file_verified failure", () => {
  it("moves to declined and reports the reason", () => {
    const file = new File(["x"], "a.txt");
    const { sender, onError } = build([file]);
    sender.start();
    sender.handleControlMessage({ type: "decline", transfer_id: "t1", reason: "no thanks" });
    expect(sender.getPhase()).toBe("declined");
    expect(onError).toHaveBeenCalledWith(expect.stringContaining("no thanks"));
  });

  it("moves to cancelled on a cancel message", async () => {
    const file = new File(["0123456789"], "a.txt");
    const { sender } = build([file]);
    sender.start();
    sender.handleControlMessage({ type: "cancel", transfer_id: "t1", reason: "changed my mind" });
    expect(sender.getPhase()).toBe("cancelled");
  });

  it("fails the transfer if the receiver reports file_verified: false", async () => {
    const file = new File(["0123456789"], "a.txt");
    const { sender, onError } = build([file]);
    sender.start();
    sender.handleControlMessage(accept());
    await vi.waitFor(() => expect(sender.getPhase()).toBe("completed"));

    sender.handleControlMessage({ type: "file_verified", file: 0, ok: false });
    expect(sender.getPhase()).toBe("failed");
    expect(onError).toHaveBeenCalled();
  });
});

describe("backpressure", () => {
  it("pauses sending when bufferedAmount is above the high watermark and resumes on bufferedamountlow", async () => {
    const file = new File(["0123456789"], "a.txt");
    const { sender, data } = build([file]);
    data.bufferedAmount = 5 * 1024 * 1024; // above the 4 MiB high watermark

    sender.start();
    sender.handleControlMessage(accept());

    // Give the async send loop a chance to run and get stuck waiting.
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(data.sentFrames).toHaveLength(0);
    expect(sender.getPhase()).toBe("sending");

    data.bufferedAmount = 0;
    data.fireBufferedAmountLow();

    await vi.waitFor(() => expect(sender.getPhase()).toBe("completed"));
    expect(data.sentFrames.length).toBeGreaterThan(0);
  });
});
