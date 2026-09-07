import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { FrameError, createFrame, packFrame, unpackFrame } from "./framing";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const VECTORS_DIR = path.resolve(__dirname, "../../../../tests/vectors");

interface FrameVector {
  file_index: number;
  offset: string; // patched to a string below -- see loadVectors()
  payload_hex: string;
  last_of_block: boolean;
  packed_hex: string;
}

function hexToBytes(hex: string): Uint8Array {
  const bytes = new Uint8Array(hex.length / 2);
  for (let i = 0; i < bytes.length; i++) {
    bytes[i] = Number.parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  }
  return bytes;
}

function bytesToHex(bytes: Uint8Array): string {
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

function loadVectors(): FrameVector[] {
  const raw = readFileSync(path.join(VECTORS_DIR, "frames.json"), "utf-8");
  // The "offset" field can exceed Number.MAX_SAFE_INTEGER (the vectors deliberately
  // include 2**64-1), which JSON.parse would silently round. Quoting it first lets us
  // read it as a string and convert to BigInt ourselves, losslessly.
  const patched = raw.replace(/"offset":\s*(\d+)/g, '"offset":"$1"');
  return JSON.parse(patched).cases;
}

describe("frame codec golden vectors", () => {
  const cases = loadVectors();

  it("has at least one vector", () => {
    expect(cases.length).toBeGreaterThan(0);
  });

  it.each(cases)("packs %j to the pinned bytes", (testCase) => {
    const frame = createFrame(
      testCase.file_index,
      BigInt(testCase.offset),
      hexToBytes(testCase.payload_hex),
      testCase.last_of_block,
    );
    expect(bytesToHex(packFrame(frame))).toBe(testCase.packed_hex);
  });

  it.each(cases)("round-trips %j through unpackFrame", (testCase) => {
    const packed = hexToBytes(testCase.packed_hex);
    const unpacked = unpackFrame(packed);
    expect(unpacked.fileIndex).toBe(testCase.file_index);
    expect(unpacked.offset).toBe(BigInt(testCase.offset));
    expect(bytesToHex(unpacked.payload)).toBe(testCase.payload_hex);
    expect(unpacked.lastOfBlock).toBe(testCase.last_of_block);
  });
});

describe("createFrame validation", () => {
  it("rejects a file index above uint32 max", () => {
    expect(() => createFrame(2 ** 32, 0n, new Uint8Array())).toThrow(FrameError);
  });

  it("rejects a negative file index", () => {
    expect(() => createFrame(-1, 0n, new Uint8Array())).toThrow(FrameError);
  });

  it("rejects an offset above uint64 max", () => {
    expect(() => createFrame(0, 2n ** 64n, new Uint8Array())).toThrow(FrameError);
  });

  it("rejects a negative offset", () => {
    expect(() => createFrame(0, -1n, new Uint8Array())).toThrow(FrameError);
  });
});

describe("unpackFrame error cases", () => {
  it("rejects a frame shorter than the header", () => {
    expect(() => unpackFrame(new Uint8Array(10))).toThrow(FrameError);
  });

  it("rejects bad magic bytes", () => {
    const frame = createFrame(0, 0n, new Uint8Array());
    const packed = packFrame(frame);
    packed[0] = 0xff;
    expect(() => unpackFrame(packed)).toThrow(FrameError);
  });

  it("rejects an unsupported version", () => {
    const frame = createFrame(0, 0n, new Uint8Array());
    const packed = packFrame(frame);
    packed[2] = 99;
    expect(() => unpackFrame(packed)).toThrow(FrameError);
  });
});
