/**
 * Dedicated worker owning OPFS `FileSystemSyncAccessHandle`s for one transfer
 * (docs/adr/006-receiver-storage-opfs.md). Sync access handles are synchronous and
 * fast, which is why the spec confines them to a worker rather than the main thread --
 * this file is loaded via `new Worker(new URL("./opfs-worker.ts", import.meta.url))`
 * (see opfs-storage.ts), never imported directly.
 *
 * This file needs `lib: ["WebWorker"]`, not `"DOM"` (see ../../../tsconfig.worker.json)
 * -- TypeScript's DOM and WebWorker libs both declare a conflicting global scope, so
 * they can't be combined in one compiler run, and this is the one file in the web
 * client that runs in a worker rather than the main thread.
 */

import type { WorkerRequest, WorkerResponse } from "./opfs-protocol";

const handles = new Map<string, FileSystemSyncAccessHandle>();

function key(transferId: string, fileIndex: number): string {
  return `${transferId}:${fileIndex}`;
}

async function getTransferDirectory(transferId: string): Promise<FileSystemDirectoryHandle> {
  const root = await navigator.storage.getDirectory();
  return root.getDirectoryHandle(`beam-transfer-${transferId}`, { create: true });
}

async function handleRequest(request: WorkerRequest): Promise<ArrayBuffer | undefined> {
  switch (request.type) {
    case "open": {
      const dir = await getTransferDirectory(request.transferId);
      const fileHandle = await dir.getFileHandle(String(request.fileIndex), { create: true });
      const accessHandle = await fileHandle.createSyncAccessHandle();
      accessHandle.truncate(request.size);
      handles.set(key(request.transferId, request.fileIndex), accessHandle);
      return undefined;
    }
    case "write": {
      const handle = handles.get(key(request.transferId, request.fileIndex));
      if (!handle) throw new Error(`file ${request.fileIndex} is not open`);
      handle.write(new Uint8Array(request.data), { at: request.offset });
      return undefined;
    }
    case "read": {
      const handle = handles.get(key(request.transferId, request.fileIndex));
      if (!handle) throw new Error(`file ${request.fileIndex} is not open`);
      const length = request.end - request.start;
      const buffer = new Uint8Array(length);
      handle.read(buffer, { at: request.start });
      return buffer.buffer;
    }
    case "close": {
      const k = key(request.transferId, request.fileIndex);
      const handle = handles.get(k);
      if (handle) {
        handle.flush();
        handle.close();
        handles.delete(k);
      }
      return undefined;
    }
    case "delete": {
      const dir = await getTransferDirectory(request.transferId);
      await dir.removeEntry(String(request.fileIndex)).catch(() => undefined);
      return undefined;
    }
    case "deleteAll": {
      const root = await navigator.storage.getDirectory();
      await root.removeEntry(`beam-transfer-${request.transferId}`, { recursive: true }).catch(() => undefined);
      return undefined;
    }
  }
}

self.onmessage = (event: MessageEvent<WorkerRequest>) => {
  const request = event.data;
  handleRequest(request)
    .then((data) => {
      const response: WorkerResponse = { id: request.id, ok: true, data };
      self.postMessage(response, data ? [data] : []);
    })
    .catch((err: unknown) => {
      const response: WorkerResponse = {
        id: request.id,
        ok: false,
        error: err instanceof Error ? err.message : String(err),
      };
      self.postMessage(response);
    });
};
