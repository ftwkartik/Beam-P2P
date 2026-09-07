import { describe, expect, it } from "vitest";

import { bytesToHex, deriveFileRootHash, hashBytes, hashBytesHex, hexToBytes } from "./hashing";

const encoder = new TextEncoder();

describe("hashBytesHex / hashBytes", () => {
  it("matches the well-known SHA-256('abc') test vector", async () => {
    const hex = await hashBytesHex(encoder.encode("abc"));
    expect(hex).toBe("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");  // pragma: allowlist secret
  });

  it("matches SHA-256 of the empty string", async () => {
    const hex = await hashBytesHex(new Uint8Array());
    expect(hex).toBe("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");  // pragma: allowlist secret
  });

  it("hashBytes returns the same digest as raw bytes", async () => {
    const bytes = await hashBytes(encoder.encode("abc"));
    expect(bytesToHex(bytes)).toBe("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");  // pragma: allowlist secret
  });

  it("can be called repeatedly without cross-contaminating state", async () => {
    const a = await hashBytesHex(encoder.encode("abc"));
    const b = await hashBytesHex(new Uint8Array());
    const aAgain = await hashBytesHex(encoder.encode("abc"));
    expect(a).toBe(aAgain);
    expect(b).toBe("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");  // pragma: allowlist secret
  });
});

describe("hex/bytes round trip", () => {
  it("round-trips arbitrary bytes", () => {
    const bytes = new Uint8Array([0, 1, 255, 16, 128]);
    expect(hexToBytes(bytesToHex(bytes))).toEqual(bytes);
  });
});

describe("deriveFileRootHash", () => {
  // Reference values computed independently with Python's hashlib:
  //   hashlib.sha256(b"beam-file-v1" + size.to_bytes(8, "big") + h0 + h1).hexdigest()
  it("matches an independently computed reference value", async () => {
    const h0 = await hashBytes(encoder.encode("a"));
    const h1 = await hashBytes(encoder.encode("b"));
    const root = await deriveFileRootHash(13n, [h0, h1]);
    expect(root).toBe("c091ceaa9e2f4f242a40d2cf7bbdb4b3a13a157a9f26724b5e5e3e9ed9cf42e2");  // pragma: allowlist secret
  });

  it("matches the reference value for a zero-byte file with no blocks", async () => {
    const root = await deriveFileRootHash(0n, []);
    expect(root).toBe("c6bb6e55377c307e905cf1106121b75e7afec076c05cac943803beb1ca326909");  // pragma: allowlist secret
  });

  it("changes if the block order changes", async () => {
    const h0 = await hashBytes(encoder.encode("a"));
    const h1 = await hashBytes(encoder.encode("b"));
    const forward = await deriveFileRootHash(13n, [h0, h1]);
    const reversed = await deriveFileRootHash(13n, [h1, h0]);
    expect(forward).not.toBe(reversed);
  });

  it("changes if the declared size changes", async () => {
    const h0 = await hashBytes(encoder.encode("a"));
    const a = await deriveFileRootHash(13n, [h0]);
    const b = await deriveFileRootHash(14n, [h0]);
    expect(a).not.toBe(b);
  });
});
