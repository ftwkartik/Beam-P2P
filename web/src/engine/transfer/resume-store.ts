/**
 * Persists per-file verified-block bitmaps (and their block hashes) across a
 * reconnect or a full page reload, so a receiver can resume instead of starting
 * over (docs/protocol.md §4.3, docs/adr/005-transfer-protocol.md "Resume state
 * lives with the receiver"). Only meaningful when the bytes behind the bitmap are
 * themselves durable -- see `storage/types.ts`'s `TransferStorage.durable` -- so
 * `transfer/receiver.ts` gates every read and write of this store on that flag.
 */

export interface PersistedFileState {
  size: number;
  bitmapBase64: string;
  /** One hex string per block, or null for a block not yet verified. Indexed by
   * block index, same as `BlockBitmap`. */
  blockHashesHex: (string | null)[];
}

export interface ResumeStore {
  /** Whether the receiver already showed the consent prompt and the user accepted
   * this transfer id in a previous connection -- so a resumed `offer_files` (the
   * sender re-sending after a reconnect) can skip asking again. */
  wasAccepted(transferId: string): Promise<boolean>;
  markAccepted(transferId: string): Promise<void>;
  loadFile(transferId: string, fileIndex: number): Promise<PersistedFileState | null>;
  saveFile(transferId: string, fileIndex: number, state: PersistedFileState): Promise<void>;
  /** Removes every record for a transfer: called once it completes, is declined or
   * is cancelled, so IndexedDB doesn't accumulate rows for finished transfers. */
  clearTransfer(transferId: string): Promise<void>;
}

/** The default when no store is configured (e.g. most unit tests): resume is simply
 * never attempted, which is always correct, just not durable across a reload. */
export class NullResumeStore implements ResumeStore {
  async wasAccepted(): Promise<boolean> {
    return false;
  }

  async markAccepted(): Promise<void> {
    // no-op
  }

  async loadFile(): Promise<PersistedFileState | null> {
    return null;
  }

  async saveFile(): Promise<void> {
    // no-op
  }

  async clearTransfer(): Promise<void> {
    // no-op
  }
}

const DB_NAME = "beam-resume";
const DB_VERSION = 1;
const ACCEPTED_STORE = "accepted";
const FILES_STORE = "files";

function fileKey(transferId: string, fileIndex: number): string {
  return `${transferId}:${fileIndex}`;
}

/** The real, browser-backed store. Uses IndexedDB directly (no wrapper library) --
 * the schema is two tiny key-value stores, not worth a dependency for. */
export class IndexedDbResumeStore implements ResumeStore {
  private dbPromise: Promise<IDBDatabase> | null = null;

  private openDb(): Promise<IDBDatabase> {
    this.dbPromise ??= new Promise((resolve, reject) => {
      const request = indexedDB.open(DB_NAME, DB_VERSION);
      request.onupgradeneeded = () => {
        const db = request.result;
        if (!db.objectStoreNames.contains(ACCEPTED_STORE)) db.createObjectStore(ACCEPTED_STORE);
        if (!db.objectStoreNames.contains(FILES_STORE)) db.createObjectStore(FILES_STORE);
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error ?? new Error("failed to open the resume IndexedDB"));
    });
    return this.dbPromise;
  }

  private async run<T>(
    storeName: string,
    mode: IDBTransactionMode,
    fn: (store: IDBObjectStore) => IDBRequest<T>,
  ): Promise<T> {
    const db = await this.openDb();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(storeName, mode);
      const request = fn(tx.objectStore(storeName));
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error ?? new Error(`IndexedDB request on ${storeName} failed`));
    });
  }

  async wasAccepted(transferId: string): Promise<boolean> {
    const value = await this.run<boolean | undefined>(ACCEPTED_STORE, "readonly", (store) => store.get(transferId));
    return value === true;
  }

  async markAccepted(transferId: string): Promise<void> {
    await this.run(ACCEPTED_STORE, "readwrite", (store) => store.put(true, transferId));
  }

  async loadFile(transferId: string, fileIndex: number): Promise<PersistedFileState | null> {
    const value = await this.run<PersistedFileState | undefined>(FILES_STORE, "readonly", (store) =>
      store.get(fileKey(transferId, fileIndex)),
    );
    return value ?? null;
  }

  async saveFile(transferId: string, fileIndex: number, state: PersistedFileState): Promise<void> {
    await this.run(FILES_STORE, "readwrite", (store) => store.put(state, fileKey(transferId, fileIndex)));
  }

  async clearTransfer(transferId: string): Promise<void> {
    const db = await this.openDb();
    const prefix = `${transferId}:`;
    await Promise.all([
      new Promise<void>((resolve, reject) => {
        const tx = db.transaction(ACCEPTED_STORE, "readwrite");
        const request = tx.objectStore(ACCEPTED_STORE).delete(transferId);
        request.onsuccess = () => resolve();
        request.onerror = () => reject(request.error ?? new Error("failed to clear accepted flag"));
      }),
      new Promise<void>((resolve, reject) => {
        const tx = db.transaction(FILES_STORE, "readwrite");
        const store = tx.objectStore(FILES_STORE);
        const cursorRequest = store.openCursor();
        cursorRequest.onsuccess = () => {
          const cursor = cursorRequest.result;
          if (!cursor) {
            resolve();
            return;
          }
          if (typeof cursor.key === "string" && cursor.key.startsWith(prefix)) cursor.delete();
          cursor.continue();
        };
        cursorRequest.onerror = () => reject(cursorRequest.error ?? new Error("failed to clear file records"));
      }),
    ]);
  }
}
