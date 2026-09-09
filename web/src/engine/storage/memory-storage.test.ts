import { describe, expect, it } from "vitest";

import { MemoryStorage } from "./memory-storage";

describe("MemoryStorage", () => {
  it("writes and reads back bytes at an offset", async () => {
    const storage = new MemoryStorage();
    const handle = await storage.openFile(0, 10);

    await handle.writeAt(2, new Uint8Array([1, 2, 3]));
    const range = await handle.readRange(0, 10);
    expect(Array.from(range)).toEqual([0, 0, 1, 2, 3, 0, 0, 0, 0, 0]);
  });

  it("accepts out-of-order writes and reassembles them correctly", async () => {
    const storage = new MemoryStorage();
    const handle = await storage.openFile(0, 6);

    await handle.writeAt(3, new Uint8Array([4, 5, 6]));
    await handle.writeAt(0, new Uint8Array([1, 2, 3]));

    expect(Array.from(await handle.readRange(0, 6))).toEqual([1, 2, 3, 4, 5, 6]);
  });

  it("rejects a write that would run past the end of the file", async () => {
    const storage = new MemoryStorage();
    const handle = await storage.openFile(0, 4);
    await expect(handle.writeAt(2, new Uint8Array([1, 2, 3]))).rejects.toThrow(RangeError);
  });

  it("finalize returns a Blob of exactly the declared size", async () => {
    const storage = new MemoryStorage();
    const handle = await storage.openFile(0, 5);
    await handle.writeAt(0, new Uint8Array([1, 2, 3, 4, 5]));

    const blob = await handle.finalize();
    expect(blob.size).toBe(5);
    expect(Array.from(new Uint8Array(await blob.arrayBuffer()))).toEqual([1, 2, 3, 4, 5]);
  });

  it("keeps files independent by index", async () => {
    const storage = new MemoryStorage();
    const a = await storage.openFile(0, 3);
    const b = await storage.openFile(1, 3);

    await a.writeAt(0, new Uint8Array([1, 1, 1]));
    await b.writeAt(0, new Uint8Array([2, 2, 2]));

    expect(Array.from(await a.readRange(0, 3))).toEqual([1, 1, 1]);
    expect(Array.from(await b.readRange(0, 3))).toEqual([2, 2, 2]);
  });

  it("rejects operations on a closed handle", async () => {
    const storage = new MemoryStorage();
    const handle = await storage.openFile(0, 3);
    await handle.close();
    await expect(handle.writeAt(0, new Uint8Array([1]))).rejects.toThrow();
  });

  it("deleteFile and deleteAll remove tracked handles without throwing", async () => {
    const storage = new MemoryStorage();
    await storage.openFile(0, 3);
    await storage.openFile(1, 3);
    await storage.deleteFile(0);
    await storage.deleteAll();
  });
});
