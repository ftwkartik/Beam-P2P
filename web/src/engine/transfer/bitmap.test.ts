import { describe, expect, it } from "vitest";

import { BlockBitmap, indicesToRanges, rangesToIndices } from "./bitmap";

describe("BlockBitmap", () => {
  it("starts with nothing set", () => {
    const bitmap = new BlockBitmap(10);
    expect(bitmap.count()).toBe(0);
    expect(bitmap.isComplete()).toBe(false);
    for (let i = 0; i < 10; i++) expect(bitmap.has(i)).toBe(false);
  });

  it("tracks set blocks", () => {
    const bitmap = new BlockBitmap(10);
    bitmap.set(0);
    bitmap.set(3);
    bitmap.set(9);
    expect(bitmap.has(0)).toBe(true);
    expect(bitmap.has(1)).toBe(false);
    expect(bitmap.has(3)).toBe(true);
    expect(bitmap.has(9)).toBe(true);
    expect(bitmap.count()).toBe(3);
  });

  it("is complete once every block is set", () => {
    const bitmap = new BlockBitmap(4);
    for (let i = 0; i < 4; i++) bitmap.set(i);
    expect(bitmap.isComplete()).toBe(true);
  });

  it("handles a zero-block bitmap as trivially complete", () => {
    const bitmap = new BlockBitmap(0);
    expect(bitmap.isComplete()).toBe(true);
  });

  it("rejects an out-of-range index", () => {
    const bitmap = new BlockBitmap(4);
    expect(() => bitmap.set(4)).toThrow(RangeError);
    expect(() => bitmap.set(-1)).toThrow(RangeError);
    expect(() => bitmap.has(4)).toThrow(RangeError);
  });

  it("round-trips through base64", () => {
    const bitmap = new BlockBitmap(20);
    for (const i of [0, 1, 5, 8, 19]) bitmap.set(i);

    const restored = BlockBitmap.fromBase64(bitmap.toBase64(), 20);
    for (let i = 0; i < 20; i++) expect(restored.has(i)).toBe(bitmap.has(i));
  });

  it("fromBase64 with an empty string is an all-clear bitmap", () => {
    const bitmap = BlockBitmap.fromBase64("", 8);
    expect(bitmap.count()).toBe(0);
  });

  it("toRanges collapses contiguous runs", () => {
    const bitmap = new BlockBitmap(10);
    for (const i of [0, 1, 2, 5, 7, 8, 9]) bitmap.set(i);
    expect(bitmap.toRanges()).toEqual([
      { start: 0, end: 2 },
      { start: 5, end: 5 },
      { start: 7, end: 9 },
    ]);
  });

  it("applyRanges sets every index in each range", () => {
    const bitmap = new BlockBitmap(10);
    bitmap.applyRanges([
      { start: 2, end: 4 },
      { start: 8, end: 8 },
    ]);
    expect([...Array(10).keys()].filter((i) => bitmap.has(i))).toEqual([2, 3, 4, 8]);
  });
});

describe("indicesToRanges / rangesToIndices", () => {
  it("collapses a sorted list", () => {
    expect(indicesToRanges([0, 1, 2, 4, 6, 7])).toEqual([
      { start: 0, end: 2 },
      { start: 4, end: 4 },
      { start: 6, end: 7 },
    ]);
  });

  it("handles an unsorted list with duplicates", () => {
    expect(indicesToRanges([5, 1, 2, 1, 0])).toEqual([{ start: 0, end: 2 }, { start: 5, end: 5 }]);
  });

  it("returns an empty array for no indices", () => {
    expect(indicesToRanges([])).toEqual([]);
  });

  it("round-trips through rangesToIndices", () => {
    const indices = [0, 1, 2, 5, 9, 10, 11];
    expect(rangesToIndices(indicesToRanges(indices))).toEqual(indices);
  });
});
