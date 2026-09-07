/**
 * Per-block SHA-256 and the file root hash (docs/protocol.md §4.3;
 * docs/adr/005-transfer-protocol.md). Uses hash-wasm rather than the native Web
 * Crypto SubtleCrypto (used elsewhere for the one-off SAS hash): ADR-005 calls out
 * hash-wasm specifically for bulk per-block hashing performance across a whole
 * transfer, and hash-wasm's incremental hasher lets one WASM instance be reused
 * across every block instead of paying setup cost per call.
 *
 * A block hash is the raw 32-byte SHA-256 digest of the block's bytes. The file root
 * hash is `SHA-256("beam-file-v1" || size_u64 || h_0 || h_1 || ... || h_n)` -- a root
 * over the block hashes rather than a plain streaming hash of the file, because blocks
 * can arrive (and be hashed, for resume) out of order.
 */

import { createSHA256, type IHasher } from "hash-wasm";

const FILE_ROOT_DOMAIN = new TextEncoder().encode("beam-file-v1");

let hasherPromise: Promise<IHasher> | null = null;

async function getHasher(): Promise<IHasher> {
  hasherPromise ??= createSHA256();
  return hasherPromise;
}

/** The raw 32-byte SHA-256 digest of `data`. */
export async function hashBytes(data: Uint8Array): Promise<Uint8Array> {
  const hasher = await getHasher();
  hasher.init();
  hasher.update(data);
  return hasher.digest("binary");
}

/** The lowercase hex SHA-256 digest of `data`, as sent on the wire (docs/protocol.md §4.1). */
export async function hashBytesHex(data: Uint8Array): Promise<string> {
  const hasher = await getHasher();
  hasher.init();
  hasher.update(data);
  return hasher.digest("hex");
}

export function bytesToHex(bytes: Uint8Array): string {
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

export function hexToBytes(hex: string): Uint8Array {
  const bytes = new Uint8Array(hex.length / 2);
  for (let i = 0; i < bytes.length; i++) {
    bytes[i] = Number.parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  }
  return bytes;
}

/**
 * The file root hash over `blockHashes` (each a raw 32-byte digest, in block order),
 * as a lowercase hex string (docs/protocol.md §4.3).
 */
export async function deriveFileRootHash(
  sizeBytes: bigint,
  blockHashes: readonly Uint8Array[],
): Promise<string> {
  const sizeField = new Uint8Array(8);
  new DataView(sizeField.buffer).setBigUint64(0, sizeBytes, false);

  const totalLength =
    FILE_ROOT_DOMAIN.length + sizeField.length + blockHashes.reduce((sum, h) => sum + h.length, 0);
  const message = new Uint8Array(totalLength);

  let offset = 0;
  message.set(FILE_ROOT_DOMAIN, offset);
  offset += FILE_ROOT_DOMAIN.length;
  message.set(sizeField, offset);
  offset += sizeField.length;
  for (const hash of blockHashes) {
    message.set(hash, offset);
    offset += hash.length;
  }

  return hashBytesHex(message);
}
