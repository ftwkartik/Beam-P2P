/**
 * The receiving side of a file transfer (docs/protocol.md §4). Framework-free and
 * storage-agnostic (docs/engine/storage/types.ts's `TransferStorage`); consent is a
 * UI decision, surfaced via `onOffer` and acted on by calling `accept()`/`decline()`.
 */

import type {
  BlockMessage,
  FileDoneMessage,
  FileOffer,
  OfferFilesMessage,
  PeerMessage,
} from "../../protocol/generated/peer-message";
import { BlockBitmap, indicesToRanges } from "./bitmap";
import { unpackFrame } from "./framing";
import { deriveFileRootHash, hashBytes, hashBytesHex } from "./hashing";
import type { ControlChannelLike } from "./channels";
import type { TransferStorage, FileStorageHandle } from "../storage/types";

/** docs/protocol.md §4.1: ack ranges are batched, every 8 blocks or 250 ms. */
const ACK_BATCH_SIZE = 8;
const ACK_BATCH_MS = 250;

export type ReceiverPhase =
  | "waiting_for_offer"
  | "offered"
  | "declined"
  | "receiving"
  | "completed"
  | "failed"
  | "cancelled";

export interface OfferedManifest {
  transferId: string;
  blockSize: number;
  files: FileOffer[];
}

export interface ReceiverProgress {
  fileIndex: number;
  fileCount: number;
  bytesVerifiedForFile: number;
  fileSize: number;
  totalBytesVerified: number;
  totalBytes: number;
}

export interface FileReadyResult {
  fileIndex: number;
  offer: FileOffer;
  blob: Blob;
}

export interface TransferReceiverOptions {
  storage: TransferStorage;
  control: ControlChannelLike;
  onOffer?: (manifest: OfferedManifest) => void;
  onPhaseChange?: (phase: ReceiverPhase) => void;
  onProgress?: (progress: ReceiverProgress) => void;
  onFileReady?: (result: FileReadyResult) => void;
  onError?: (message: string) => void;
}

interface FileState {
  offer: FileOffer;
  handle: FileStorageHandle;
  blockCount: number;
  bitmap: BlockBitmap;
  blockHashes: Uint8Array[];
  announcedBlockHashes: Map<number, string>;
  bytesReceivedByBlock: Map<number, number>;
  pendingAckIndices: number[];
  ackTimer: ReturnType<typeof setTimeout> | null;
  expectedRootHash: string | null;
  verified: boolean;
}

export class TransferReceiver {
  private readonly storage: TransferStorage;
  private readonly control: ControlChannelLike;
  private readonly onOffer?: (manifest: OfferedManifest) => void;
  private readonly onPhaseChange?: (phase: ReceiverPhase) => void;
  private readonly onProgress?: (progress: ReceiverProgress) => void;
  private readonly onFileReady?: (result: FileReadyResult) => void;
  private readonly onError?: (message: string) => void;

  private phase: ReceiverPhase = "waiting_for_offer";
  private manifest: OfferedManifest | null = null;
  private readonly filesByIndex = new Map<number, FileState>();
  private readonly fileStatePromises = new Map<number, Promise<FileState>>();
  private totalBytes = 0;
  private totalBytesVerified = 0;

  constructor(options: TransferReceiverOptions) {
    this.storage = options.storage;
    this.control = options.control;
    this.onOffer = options.onOffer;
    this.onPhaseChange = options.onPhaseChange;
    this.onProgress = options.onProgress;
    this.onFileReady = options.onFileReady;
    this.onError = options.onError;
  }

  handleControlMessage(message: PeerMessage): void {
    switch (message.type) {
      case "offer_files":
        this.handleOffer(message);
        break;
      case "block":
        this.handleBlockAnnouncement(message);
        break;
      case "file_done":
        void this.handleFileDone(message);
        break;
      case "cancel":
        this.setPhase("cancelled");
        break;
      default:
        break;
    }
  }

  /** Feeds in one binary frame received on the `data` channel. */
  handleDataFrame(raw: Uint8Array): void {
    void this.processFrame(raw);
  }

  /** Accepts the pending offer (call after `onOffer` and user consent). */
  accept(): void {
    if (!this.manifest) return;
    this.setPhase("receiving");
    this.control.send(
      JSON.stringify({ type: "accept", transfer_id: this.manifest.transferId, have: {} }),
    );
  }

  decline(reason: string): void {
    if (!this.manifest) return;
    this.setPhase("declined");
    this.control.send(
      JSON.stringify({ type: "decline", transfer_id: this.manifest.transferId, reason }),
    );
  }

  getPhase(): ReceiverPhase {
    return this.phase;
  }

  private handleOffer(message: OfferFilesMessage): void {
    this.manifest = { transferId: message.transfer_id, blockSize: message.block_size, files: message.files };
    this.totalBytes = message.files.reduce((sum, f) => sum + f.size, 0);
    this.setPhase("offered");
    this.onOffer?.(this.manifest);
  }

  /** Memoizes the in-flight *promise*, not just the resolved state: a block
   * announcement and a data frame for the same new file can both call this before
   * either's `storage.openFile()` resolves, and without this they'd race to create
   * two separate handles for the same file. */
  private ensureFileState(fileIndex: number): Promise<FileState> {
    let promise = this.fileStatePromises.get(fileIndex);
    if (!promise) {
      promise = this.createFileState(fileIndex);
      this.fileStatePromises.set(fileIndex, promise);
    }
    return promise;
  }

  private async createFileState(fileIndex: number): Promise<FileState> {
    const offer = this.manifest?.files.find((f) => f.index === fileIndex);
    if (!offer || !this.manifest) throw new Error(`no manifest entry for file ${fileIndex}`);

    const blockCount = offer.size === 0 ? 0 : Math.ceil(offer.size / this.manifest.blockSize);
    const state: FileState = {
      offer,
      handle: await this.storage.openFile(fileIndex, offer.size),
      blockCount,
      bitmap: new BlockBitmap(blockCount),
      blockHashes: new Array(blockCount),
      announcedBlockHashes: new Map(),
      bytesReceivedByBlock: new Map(),
      pendingAckIndices: [],
      ackTimer: null,
      expectedRootHash: null,
      verified: false,
    };
    this.filesByIndex.set(fileIndex, state);
    return state;
  }

  private handleBlockAnnouncement(message: BlockMessage): void {
    void this.ensureFileState(message.file).then((state) => {
      state.announcedBlockHashes.set(message.index, message.sha256);
    });
  }

  private blockRange(state: FileState, blockIndex: number): { start: number; end: number } {
    const start = blockIndex * (this.manifest?.blockSize ?? 0);
    const end = Math.min(start + (this.manifest?.blockSize ?? 0), state.offer.size);
    return { start, end };
  }

  private async processFrame(raw: Uint8Array): Promise<void> {
    try {
      const frame = unpackFrame(raw);
      const state = await this.ensureFileState(frame.fileIndex);
      // Safe: file sizes are capped at MAX_FILE_SIZE (Number.MAX_SAFE_INTEGER); only
      // the wire format itself needs the full uint64 range framing.ts's bigint covers.
      const offset = Number(frame.offset);

      await state.handle.writeAt(offset, frame.payload);

      const blockIndex = Math.floor(offset / this.manifest!.blockSize);
      const received = (state.bytesReceivedByBlock.get(blockIndex) ?? 0) + frame.payload.length;
      state.bytesReceivedByBlock.set(blockIndex, received);

      const { start, end } = this.blockRange(state, blockIndex);
      if (received >= end - start) {
        await this.verifyBlock(state, blockIndex, start, end);
      }
    } catch (err) {
      this.onError?.(err instanceof Error ? err.message : String(err));
    }
  }

  private async verifyBlock(state: FileState, blockIndex: number, start: number, end: number): Promise<void> {
    const announced = state.announcedBlockHashes.get(blockIndex);
    if (!announced) return; // the block message hasn't arrived yet; re-checked once it does

    const bytes = await state.handle.readRange(start, end);
    const [hashHex, hashBinary] = await Promise.all([hashBytesHex(bytes), hashBytes(bytes)]);

    if (hashHex !== announced) {
      state.bytesReceivedByBlock.set(blockIndex, 0);
      this.control.send(
        JSON.stringify({ type: "nack", file: state.offer.index, index: blockIndex, reason: "hash_mismatch" }),
      );
      return;
    }

    state.blockHashes[blockIndex] = hashBinary;
    state.bitmap.set(blockIndex);
    this.totalBytesVerified += end - start;
    this.queueAck(state, blockIndex);
    this.reportProgress(state);

    if (state.bitmap.isComplete() && state.expectedRootHash !== null) {
      await this.finalizeFile(state);
    }
  }

  private queueAck(state: FileState, blockIndex: number): void {
    state.pendingAckIndices.push(blockIndex);
    if (state.pendingAckIndices.length >= ACK_BATCH_SIZE) {
      this.flushAck(state);
      return;
    }
    state.ackTimer ??= setTimeout(() => this.flushAck(state), ACK_BATCH_MS);
  }

  private flushAck(state: FileState): void {
    if (state.ackTimer !== null) {
      clearTimeout(state.ackTimer);
      state.ackTimer = null;
    }
    if (state.pendingAckIndices.length === 0) return;
    const ranges = indicesToRanges(state.pendingAckIndices);
    state.pendingAckIndices = [];
    this.control.send(JSON.stringify({ type: "ack", file: state.offer.index, verified: ranges }));
  }

  private reportProgress(state: FileState): void {
    const bytesVerifiedForFile = state.bitmap.count() === state.blockCount
      ? state.offer.size
      : sumVerifiedBytes(state, this.manifest?.blockSize ?? 0);
    this.onProgress?.({
      fileIndex: state.offer.index,
      fileCount: this.manifest?.files.length ?? 0,
      bytesVerifiedForFile,
      fileSize: state.offer.size,
      totalBytesVerified: this.totalBytesVerified,
      totalBytes: this.totalBytes,
    });
  }

  private async handleFileDone(message: FileDoneMessage): Promise<void> {
    const state = await this.ensureFileState(message.file);
    state.expectedRootHash = message.sha256;
    if (state.bitmap.isComplete()) {
      await this.finalizeFile(state);
    }
  }

  private async finalizeFile(state: FileState): Promise<void> {
    if (state.verified || state.expectedRootHash === null) return;
    state.verified = true;
    this.flushAck(state);

    const actualRootHash = await deriveFileRootHash(BigInt(state.offer.size), state.blockHashes);
    const ok = actualRootHash === state.expectedRootHash;
    this.control.send(JSON.stringify({ type: "file_verified", file: state.offer.index, ok }));

    if (!ok) {
      this.setPhase("failed");
      this.onError?.(`File ${state.offer.index} failed root-hash verification`);
      return;
    }

    const blob = await state.handle.finalize();
    this.onFileReady?.({ fileIndex: state.offer.index, offer: state.offer, blob });

    const allFilesTouched = this.manifest !== null && this.filesByIndex.size === this.manifest.files.length;
    if (allFilesTouched && [...this.filesByIndex.values()].every((f) => f.verified)) {
      this.setPhase("completed");
    }
  }

  private setPhase(phase: ReceiverPhase): void {
    if (phase === this.phase) return;
    this.phase = phase;
    this.onPhaseChange?.(phase);
  }
}

function sumVerifiedBytes(state: FileState, blockSize: number): number {
  let bytes = 0;
  for (let i = 0; i < state.blockCount; i++) {
    if (state.bitmap.has(i)) {
      bytes += Math.min(blockSize, state.offer.size - i * blockSize);
    }
  }
  return bytes;
}
