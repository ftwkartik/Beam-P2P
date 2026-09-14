/**
 * Main-thread proxy for the OPFS-backed transfer storage
 * (docs/adr/006-receiver-storage-opfs.md). Talks to opfs-worker.ts over `postMessage`,
 * correlating requests and responses by an incrementing id, since a dedicated worker's
 * message channel has no built-in request/response pairing.
 *
 * Unit-testable via an injectable `workerFactory` (same pattern as SignalingClient's
 * `webSocketFactory` and PeerConnection's `rtcFactory`): jsdom has no OPFS and no real
 * Worker execution, so this proxy's own message protocol is what gets tested here --
 * the worker's actual OPFS calls need a real browser (docs/roadmap.md Milestone 8's
 * manual-verification note).
 */

import type { DistributiveOmit, WorkerRequest, WorkerResponse } from "./opfs-protocol";
import type { FileStorageHandle, TransferStorage } from "./types";

export type WorkerFactory = () => Worker;

const defaultWorkerFactory: WorkerFactory = () =>
  new Worker(new URL("./opfs-worker.ts", import.meta.url), { type: "module" });

interface PendingCall {
  resolve: (data: ArrayBuffer | undefined) => void;
  reject: (err: Error) => void;
}

export class OpfsStorage implements TransferStorage {
  readonly durable = true;
  private readonly transferId: string;
  private readonly worker: Worker;
  private nextId = 1;
  private readonly pending = new Map<number, PendingCall>();

  constructor(transferId: string, workerFactory: WorkerFactory = defaultWorkerFactory) {
    this.transferId = transferId;
    this.worker = workerFactory();
    this.worker.addEventListener("message", (event: MessageEvent<WorkerResponse>) => {
      this.handleResponse(event.data);
    });
  }

  private handleResponse(response: WorkerResponse): void {
    const call = this.pending.get(response.id);
    if (!call) return;
    this.pending.delete(response.id);
    if (response.ok) {
      call.resolve(response.data);
    } else {
      call.reject(new Error(response.error));
    }
  }

  private call(request: DistributiveOmit<WorkerRequest, "id">): Promise<ArrayBuffer | undefined> {
    const id = this.nextId++;
    const full = { ...request, id } as WorkerRequest;
    const transferables = full.type === "write" ? [full.data] : [];
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.worker.postMessage(full, transferables);
    });
  }

  async openFile(fileIndex: number, size: number): Promise<FileStorageHandle> {
    await this.call({ type: "open", transferId: this.transferId, fileIndex, size });
    return new OpfsFileHandle(this, fileIndex, size);
  }

  async deleteFile(fileIndex: number): Promise<void> {
    await this.call({ type: "delete", transferId: this.transferId, fileIndex });
  }

  async deleteAll(): Promise<void> {
    await this.call({ type: "deleteAll", transferId: this.transferId });
  }

  /** @internal used by OpfsFileHandle */
  callForHandle(
    request: DistributiveOmit<WorkerRequest, "id" | "transferId">,
  ): Promise<ArrayBuffer | undefined> {
    return this.call({ ...request, transferId: this.transferId } as DistributiveOmit<WorkerRequest, "id">);
  }

  /**
   * Reopens a file that the worker has already closed, as a plain (non-sync-access)
   * `FileSystemFileHandle`. Unlike `callForHandle({type: "read", ...})`, `getFile()`
   * returns a File that's read lazily from disk on demand -- this is the "disk-backed,
   * not RAM" save path the ADR calls for, not a full in-memory copy pulled through
   * the worker's postMessage channel.
   */
  async finalizeFile(fileIndex: number): Promise<Blob> {
    const root = await navigator.storage.getDirectory();
    const dir = await root.getDirectoryHandle(`beam-transfer-${this.transferId}`);
    const fileHandle = await dir.getFileHandle(String(fileIndex));
    return fileHandle.getFile();
  }

  terminate(): void {
    this.worker.terminate();
  }
}

class OpfsFileHandle implements FileStorageHandle {
  readonly fileIndex: number;
  readonly size: number;
  private readonly storage: OpfsStorage;

  constructor(storage: OpfsStorage, fileIndex: number, size: number) {
    this.storage = storage;
    this.fileIndex = fileIndex;
    this.size = size;
  }

  async writeAt(offset: number, data: Uint8Array): Promise<void> {
    // The worker takes ownership of this buffer (it's a transferable), so it must be a
    // fresh copy -- the caller's own view of `data` would otherwise be detached.
    const owned = data.slice();
    await this.storage.callForHandle({
      type: "write",
      fileIndex: this.fileIndex,
      offset,
      data: owned.buffer as ArrayBuffer,
    });
  }

  async readRange(start: number, end: number): Promise<Uint8Array> {
    const data = await this.storage.callForHandle({
      type: "read",
      fileIndex: this.fileIndex,
      start,
      end,
    });
    return new Uint8Array(data ?? new ArrayBuffer(0));
  }

  async finalize(): Promise<Blob> {
    // The sync access handle must be flushed and released before the file can be
    // reopened as a plain FileSystemFileHandle (see finalizeFile's comment).
    await this.close();
    return this.storage.finalizeFile(this.fileIndex);
  }

  async close(): Promise<void> {
    await this.storage.callForHandle({ type: "close", fileIndex: this.fileIndex });
  }
}
