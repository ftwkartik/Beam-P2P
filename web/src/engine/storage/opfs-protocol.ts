/**
 * Message protocol shared between opfs-storage.ts (main thread) and opfs-worker.ts
 * (the dedicated worker). Kept in its own file, with no DOM- or WebWorker-only types,
 * so both sides can import it under their own (mutually incompatible) `lib` setting --
 * see tsconfig.worker.json's comment on why the worker can't share tsconfig.app.json.
 */

interface OpenRequest {
  id: number;
  type: "open";
  transferId: string;
  fileIndex: number;
  size: number;
}

interface WriteRequest {
  id: number;
  type: "write";
  transferId: string;
  fileIndex: number;
  offset: number;
  data: ArrayBuffer;
}

interface ReadRequest {
  id: number;
  type: "read";
  transferId: string;
  fileIndex: number;
  start: number;
  end: number;
}

interface CloseRequest {
  id: number;
  type: "close";
  transferId: string;
  fileIndex: number;
}

interface DeleteRequest {
  id: number;
  type: "delete";
  transferId: string;
  fileIndex: number;
}

interface DeleteAllRequest {
  id: number;
  type: "deleteAll";
  transferId: string;
}

export type WorkerRequest =
  | OpenRequest
  | WriteRequest
  | ReadRequest
  | CloseRequest
  | DeleteRequest
  | DeleteAllRequest;

export type WorkerResponse =
  | { id: number; ok: true; data?: ArrayBuffer }
  | { id: number; ok: false; error: string };

/**
 * `Omit` applied to a union collapses to the fields common to every member (it isn't
 * distributive), which would drop request-specific fields like `fileIndex` or `data`.
 * This distributes over the union first, omitting `K` from each member individually.
 */
export type DistributiveOmit<T, K extends PropertyKey> = T extends unknown ? Omit<T, K> : never;
