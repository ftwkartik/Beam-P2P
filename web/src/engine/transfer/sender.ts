/**
 * The sending side of a file transfer (docs/protocol.md §4). Framework-free and
 * testable against fake control/data channels (docs/testing-strategy.md's "Transfer
 * engine" test list) -- a real `RTCDataChannel` satisfies both channel interfaces
 * structurally, so no adapter is needed at the call site.
 */

import type { AcceptMessage, FileOffer, NackMessage, PeerMessage } from "../../protocol/generated/peer-message";
import { BlockBitmap } from "./bitmap";
import { createFrame, packFrame } from "./framing";
import { deriveFileRootHash, hashBytes, hashBytesHex } from "./hashing";
import { DEFAULT_BLOCK_SIZE, buildManifest } from "./manifest";

/** docs/protocol.md §4.2: the sender pauses above this and resumes at the data
 * channel's `bufferedamountlow` threshold (set by peer.ts when creating the channel). */
export const HIGH_WATERMARK_BYTES = 4 * 1024 * 1024;
export const DEFAULT_FRAME_PAYLOAD_SIZE = 64 * 1024;
const MAX_BLOCK_RETRIES = 3;

export type SenderPhase =
  | "offering"
  | "declined"
  | "sending"
  | "completed"
  | "failed"
  | "cancelled";

export interface SenderProgress {
  fileIndex: number;
  fileCount: number;
  bytesSentForFile: number;
  fileSize: number;
  totalBytesSent: number;
  totalBytes: number;
}

export interface ControlChannelLike {
  send(data: string): void;
}

export interface DataChannelLike {
  send(data: Uint8Array): void;
  readonly bufferedAmount: number;
  addEventListener(type: "bufferedamountlow", listener: () => void): void;
  removeEventListener(type: "bufferedamountlow", listener: () => void): void;
}

export interface TransferSenderOptions {
  transferId: string;
  files: readonly File[];
  control: ControlChannelLike;
  data: DataChannelLike;
  blockSize?: number;
  frameSize?: number;
  onPhaseChange?: (phase: SenderPhase) => void;
  onProgress?: (progress: SenderProgress) => void;
  /** A file failed verification, was declined, or a block failed 3 retries. */
  onError?: (message: string) => void;
}

export class TransferSender {
  private readonly transferId: string;
  private readonly files: readonly File[];
  private readonly offers: FileOffer[];
  private readonly control: ControlChannelLike;
  private readonly data: DataChannelLike;
  private readonly blockSize: number;
  private readonly frameSize: number;
  private readonly onPhaseChange?: (phase: SenderPhase) => void;
  private readonly onProgress?: (progress: SenderProgress) => void;
  private readonly onError?: (message: string) => void;

  private phase: SenderPhase = "offering";
  private readonly totalBytes: number;
  private totalBytesSent = 0;
  private readonly haveByFile = new Map<number, BlockBitmap>();
  private readonly retriesByBlock = new Map<string, number>();
  private readonly pendingResends: NackMessage[] = [];

  constructor(options: TransferSenderOptions) {
    this.transferId = options.transferId;
    this.files = options.files;
    this.control = options.control;
    this.data = options.data;
    this.blockSize = options.blockSize ?? DEFAULT_BLOCK_SIZE;
    this.frameSize = options.frameSize ?? DEFAULT_FRAME_PAYLOAD_SIZE;
    this.onPhaseChange = options.onPhaseChange;
    this.onProgress = options.onProgress;
    this.onError = options.onError;

    this.offers = buildManifest(this.files);
    this.totalBytes = this.offers.reduce((sum, f) => sum + f.size, 0);
  }

  /** Sends the `offer_files` manifest. Call once; wait for the receiver's response
   * via `handleControlMessage`. */
  start(): void {
    this.setPhase("offering");
    this.control.send(
      JSON.stringify({
        type: "offer_files",
        transfer_id: this.transferId,
        block_size: this.blockSize,
        files: this.offers,
      }),
    );
  }

  /** Feeds in a control-channel message from the receiver. */
  handleControlMessage(message: PeerMessage): void {
    switch (message.type) {
      case "accept":
        this.handleAccept(message);
        break;
      case "decline":
        this.setPhase("declined");
        this.onError?.(`Declined: ${message.reason}`);
        break;
      case "nack":
        this.pendingResends.push(message);
        void this.drainResends();
        break;
      case "file_verified":
        if (!message.ok) {
          this.setPhase("failed");
          this.onError?.(`File ${message.file} failed verification`);
        }
        break;
      case "cancel":
        this.setPhase("cancelled");
        break;
      default:
        break;
    }
  }

  private handleAccept(message: AcceptMessage): void {
    for (const [key, base64] of Object.entries(message.have ?? {})) {
      const fileIndex = Number(key);
      const offer = this.offers.find((f) => f.index === fileIndex);
      if (!offer) continue;
      this.haveByFile.set(fileIndex, BlockBitmap.fromBase64(base64, blockCountFor(offer.size, this.blockSize)));
    }
    void this.sendAllFiles();
  }

  private async sendAllFiles(): Promise<void> {
    this.setPhase("sending");
    try {
      for (const offer of this.offers) {
        if (this.phase !== "sending") return;
        await this.sendFile(offer);
      }
      if (this.phase === "sending") {
        this.control.send(JSON.stringify({ type: "transfer_done", transfer_id: this.transferId }));
        this.setPhase("completed");
      }
    } catch (err) {
      this.setPhase("failed");
      this.onError?.(err instanceof Error ? err.message : String(err));
    }
  }

  private async sendFile(offer: FileOffer): Promise<void> {
    const file = this.files[offer.index];
    const blockCount = blockCountFor(offer.size, this.blockSize);
    const have = this.haveByFile.get(offer.index);
    const blockHashes: Uint8Array[] = new Array(blockCount);
    let bytesSentForFile = 0;

    for (let blockIndex = 0; blockIndex < blockCount; blockIndex++) {
      if (this.phase !== "sending") return;

      const start = blockIndex * this.blockSize;
      const end = Math.min(start + this.blockSize, offer.size);

      if (have?.has(blockIndex)) {
        // Still read and hash a skipped block: a file changed since a previous
        // session must be detected via a root-hash mismatch (docs/protocol.md §4.3),
        // not silently trusted.
        const bytes = new Uint8Array(await file.slice(start, end).arrayBuffer());
        blockHashes[blockIndex] = await hashBytes(bytes);
        continue;
      }

      const sent = await this.sendBlock(offer.index, blockIndex, file, start, end);
      blockHashes[blockIndex] = sent.hash;
      bytesSentForFile += end - start;
      this.totalBytesSent += end - start;
      this.onProgress?.({
        fileIndex: offer.index,
        fileCount: this.offers.length,
        bytesSentForFile,
        fileSize: offer.size,
        totalBytesSent: this.totalBytesSent,
        totalBytes: this.totalBytes,
      });
    }

    const rootHash = await deriveFileRootHash(BigInt(offer.size), blockHashes);
    this.control.send(JSON.stringify({ type: "file_done", file: offer.index, sha256: rootHash }));
  }

  private async sendBlock(
    fileIndex: number,
    blockIndex: number,
    file: File,
    start: number,
    end: number,
  ): Promise<{ hash: Uint8Array }> {
    const bytes = new Uint8Array(await file.slice(start, end).arrayBuffer());
    const [hash, hashHex] = await Promise.all([hashBytes(bytes), hashBytesHex(bytes)]);

    this.control.send(JSON.stringify({ type: "block", file: fileIndex, index: blockIndex, sha256: hashHex }));
    await this.sendFrames(fileIndex, start, bytes);

    return { hash };
  }

  private async sendFrames(fileIndex: number, blockStart: number, bytes: Uint8Array): Promise<void> {
    for (let offset = 0; offset < bytes.length; offset += this.frameSize) {
      await this.waitForBufferedAmountBelowHighWatermark();
      const chunk = bytes.subarray(offset, Math.min(offset + this.frameSize, bytes.length));
      const isLast = offset + chunk.length >= bytes.length;
      const frame = createFrame(fileIndex, BigInt(blockStart + offset), chunk, isLast);
      this.data.send(packFrame(frame));
    }
  }

  private waitForBufferedAmountBelowHighWatermark(): Promise<void> {
    if (this.data.bufferedAmount <= HIGH_WATERMARK_BYTES) return Promise.resolve();
    return new Promise((resolve) => {
      const onLow = () => {
        this.data.removeEventListener("bufferedamountlow", onLow);
        resolve();
      };
      this.data.addEventListener("bufferedamountlow", onLow);
    });
  }

  private async drainResends(): Promise<void> {
    while (this.pendingResends.length > 0) {
      const message = this.pendingResends.shift();
      if (!message) continue;
      await this.resendBlock(message);
    }
  }

  private async resendBlock(message: NackMessage): Promise<void> {
    const key = `${message.file}:${message.index}`;
    const retries = this.retriesByBlock.get(key) ?? 0;
    if (retries >= MAX_BLOCK_RETRIES) {
      this.setPhase("failed");
      this.onError?.(`Block ${message.index} of file ${message.file} failed after ${MAX_BLOCK_RETRIES} retries`);
      return;
    }
    this.retriesByBlock.set(key, retries + 1);

    const offer = this.offers.find((f) => f.index === message.file);
    const file = this.files[message.file];
    if (!offer || !file) return;

    const start = message.index * this.blockSize;
    const end = Math.min(start + this.blockSize, offer.size);
    await this.sendBlock(message.file, message.index, file, start, end);
  }

  private setPhase(phase: SenderPhase): void {
    if (phase === this.phase) return;
    this.phase = phase;
    this.onPhaseChange?.(phase);
  }

  getPhase(): SenderPhase {
    return this.phase;
  }
}

function blockCountFor(size: number, blockSize: number): number {
  return size === 0 ? 0 : Math.ceil(size / blockSize);
}
