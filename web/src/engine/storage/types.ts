/**
 * Storage abstraction the receiver writes incoming frames through
 * (docs/adr/006-receiver-storage-opfs.md). `MemoryStorage` and `OpfsStorage` (an OPFS
 * worker proxy) both implement this, so `transfer/receiver.ts` and its tests never
 * depend on which backend is in use.
 */

export interface FileStorageHandle {
  readonly fileIndex: number;
  readonly size: number;
  writeAt(offset: number, data: Uint8Array): Promise<void>;
  readRange(start: number, end: number): Promise<Uint8Array>;
  /** A disk-backed (where possible) File/Blob for the user to save. */
  finalize(): Promise<Blob>;
  close(): Promise<void>;
}

export interface TransferStorage {
  openFile(fileIndex: number, size: number): Promise<FileStorageHandle>;
  deleteFile(fileIndex: number): Promise<void>;
  /** Removes every file belonging to this transfer (docs/adr/006, "Stale partials"). */
  deleteAll(): Promise<void>;
}
