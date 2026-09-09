import { describe, expect, it, vi } from "vitest";

import { OpfsStorage } from "./opfs-storage";
import type { WorkerRequest, WorkerResponse } from "./opfs-protocol";

/** A fake Worker that answers every request "ok" immediately, recording what it saw --
 * enough to test the proxy's request/response correlation and transfer-list handling
 * without real OPFS, which jsdom has no implementation of at all. */
class FakeWorker {
  sent: { message: WorkerRequest; transfer: Transferable[] }[] = [];
  private listeners: ((event: MessageEvent<WorkerResponse>) => void)[] = [];
  terminated = false;

  addEventListener(_type: string, handler: (event: MessageEvent<WorkerResponse>) => void): void {
    this.listeners.push(handler);
  }

  postMessage(message: WorkerRequest, transfer: Transferable[] = []): void {
    this.sent.push({ message, transfer });
  }

  terminate(): void {
    this.terminated = true;
  }

  respond(response: WorkerResponse): void {
    for (const listener of this.listeners) listener({ data: response } as MessageEvent<WorkerResponse>);
  }
}

function build(): { storage: OpfsStorage; worker: FakeWorker } {
  const worker = new FakeWorker();
  const storage = new OpfsStorage("transfer-1", () => worker as unknown as Worker);
  return { storage, worker };
}

describe("OpfsStorage", () => {
  it("sends an open request scoped to the transfer id", async () => {
    const { storage, worker } = build();
    const openPromise = storage.openFile(0, 100);
    expect(worker.sent[0].message).toMatchObject({
      type: "open",
      transferId: "transfer-1",
      fileIndex: 0,
      size: 100,
    });
    worker.respond({ id: worker.sent[0].message.id, ok: true });
    await expect(openPromise).resolves.toBeDefined();
  });

  it("rejects the caller's promise when the worker reports an error", async () => {
    const { storage, worker } = build();
    const openPromise = storage.openFile(0, 100);
    worker.respond({ id: worker.sent[0].message.id, ok: false, error: "disk full" });
    await expect(openPromise).rejects.toThrow("disk full");
  });

  it("correlates concurrent calls by id, not arrival order", async () => {
    const { storage, worker } = build();
    const first = storage.deleteFile(0);
    const second = storage.deleteFile(1);

    // Respond out of order: second request's id resolves before the first's.
    worker.respond({ id: worker.sent[1].message.id, ok: true });
    worker.respond({ id: worker.sent[0].message.id, ok: true });

    await expect(second).resolves.toBeUndefined();
    await expect(first).resolves.toBeUndefined();
  });

  it("writeAt transfers a fresh, owned copy of the buffer", async () => {
    const { storage, worker } = build();
    const openPromise = storage.openFile(0, 10);
    worker.respond({ id: worker.sent[0].message.id, ok: true });
    const handle = await openPromise;

    const original = new Uint8Array([1, 2, 3]);
    const writePromise = handle.writeAt(0, original);
    const writeCall = worker.sent[1];
    expect(writeCall.message).toMatchObject({ type: "write", fileIndex: 0, offset: 0 });
    expect((writeCall.message as { data: ArrayBuffer }).data).not.toBe(original.buffer);
    expect(writeCall.transfer).toEqual([(writeCall.message as { data: ArrayBuffer }).data]);

    worker.respond({ id: writeCall.message.id, ok: true });
    await expect(writePromise).resolves.toBeUndefined();
  });

  it("readRange resolves to the bytes the worker returns", async () => {
    const { storage, worker } = build();
    const openPromise = storage.openFile(0, 10);
    worker.respond({ id: worker.sent[0].message.id, ok: true });
    const handle = await openPromise;

    const readPromise = handle.readRange(0, 3);
    const readCall = worker.sent[1];
    const payload = new Uint8Array([9, 8, 7]).buffer;
    worker.respond({ id: readCall.message.id, ok: true, data: payload });

    expect(Array.from(await readPromise)).toEqual([9, 8, 7]);
  });

  it("finalize closes the handle before reopening it as a plain file", async () => {
    const { storage, worker } = build();
    const openPromise = storage.openFile(0, 10);
    worker.respond({ id: worker.sent[0].message.id, ok: true });
    const handle = await openPromise;

    const finalizeFileSpy = vi
      .spyOn(storage, "finalizeFile")
      .mockResolvedValue(new Blob(["done"]));

    const finalizePromise = handle.finalize();
    const closeCall = worker.sent[1];
    expect(closeCall.message).toMatchObject({ type: "close", fileIndex: 0 });
    worker.respond({ id: closeCall.message.id, ok: true });

    const blob = await finalizePromise;
    expect(finalizeFileSpy).toHaveBeenCalledWith(0);
    expect(await blob.text()).toBe("done");
  });

  it("terminate stops the underlying worker", () => {
    const { storage, worker } = build();
    storage.terminate();
    expect(worker.terminated).toBe(true);
  });
});
