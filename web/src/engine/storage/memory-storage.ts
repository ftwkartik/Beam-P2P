/**
 * In-memory transfer storage: the fallback for browsers without OPFS sync access
 * handles, used for files under ~200 MB (docs/adr/006-receiver-storage-opfs.md's
 * "Trade-offs"), and the default in tests since jsdom has no OPFS at all.
 */

import type { FileStorageHandle, TransferStorage } from "./types";

class MemoryFileHandle implements FileStorageHandle {
  readonly fileIndex: number;
  readonly size: number;
  private readonly bytes: Uint8Array;
  private closed = false;

  constructor(fileIndex: number, size: number) {
    this.fileIndex = fileIndex;
    this.size = size;
    this.bytes = new Uint8Array(size);
  }

  async writeAt(offset: number, data: Uint8Array): Promise<void> {
    this.assertOpen();
    if (offset < 0 || offset + data.length > this.size) {
      throw new RangeError(`write [${offset}, ${offset + data.length}) is outside file bounds [0, ${this.size})`);
    }
    this.bytes.set(data, offset);
  }

  async readRange(start: number, end: number): Promise<Uint8Array> {
    this.assertOpen();
    return this.bytes.slice(start, end);
  }

  async finalize(): Promise<Blob> {
    this.assertOpen();
    // Cast needed because @types/node's ambient Uint8Array augmentation widens
    // `.buffer` to ArrayBufferLike (it could theoretically be a SharedArrayBuffer),
    // while DOM's BlobPart requires a plain ArrayBuffer; this Uint8Array is always
    // backed by one (allocated with `new Uint8Array(size)` above).
    return new Blob([this.bytes.buffer as ArrayBuffer]);
  }

  async close(): Promise<void> {
    this.closed = true;
  }

  private assertOpen(): void {
    if (this.closed) throw new Error(`file ${this.fileIndex} is closed`);
  }
}

export class MemoryStorage implements TransferStorage {
  readonly durable = false;
  private readonly handles = new Map<number, MemoryFileHandle>();

  async openFile(fileIndex: number, size: number): Promise<FileStorageHandle> {
    const handle = new MemoryFileHandle(fileIndex, size);
    this.handles.set(fileIndex, handle);
    return handle;
  }

  async deleteFile(fileIndex: number): Promise<void> {
    this.handles.delete(fileIndex);
  }

  async deleteAll(): Promise<void> {
    this.handles.clear();
  }
}
