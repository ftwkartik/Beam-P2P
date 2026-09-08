/**
 * The receiver-held verified-block bitmap (docs/protocol.md §4.1, `accept.have` and
 * `ack.verified`; docs/adr/005-transfer-protocol.md's "resume state lives with the
 * receiver"). One bit per block, set once a block's hash has been verified.
 */

export interface BlockRange {
  start: number;
  end: number; // inclusive
}

export class BlockBitmap {
  private readonly bits: Uint8Array;
  readonly blockCount: number;

  constructor(blockCount: number) {
    if (blockCount < 0) throw new RangeError("blockCount must be >= 0");
    this.blockCount = blockCount;
    this.bits = new Uint8Array(Math.ceil(blockCount / 8));
  }

  private checkIndex(index: number): void {
    if (!Number.isInteger(index) || index < 0 || index >= this.blockCount) {
      throw new RangeError(`block index ${index} out of range [0, ${this.blockCount})`);
    }
  }

  has(index: number): boolean {
    this.checkIndex(index);
    return (this.bits[index >> 3] & (1 << (index & 7))) !== 0;
  }

  set(index: number): void {
    this.checkIndex(index);
    this.bits[index >> 3] |= 1 << (index & 7);
  }

  count(): number {
    let total = 0;
    for (const byte of this.bits) total += popcount(byte);
    return total;
  }

  isComplete(): boolean {
    return this.count() === this.blockCount;
  }

  /** All verified indices, as inclusive ranges, in ascending order. */
  toRanges(): BlockRange[] {
    const indices: number[] = [];
    for (let i = 0; i < this.blockCount; i++) {
      if (this.has(i)) indices.push(i);
    }
    return indicesToRanges(indices);
  }

  toBase64(): string {
    return bytesToBase64(this.bits);
  }

  static fromBase64(base64: string, blockCount: number): BlockBitmap {
    const bitmap = new BlockBitmap(blockCount);
    if (base64.length === 0) return bitmap;
    const bytes = base64ToBytes(base64);
    const length = Math.min(bytes.length, bitmap.bits.length);
    bitmap.bits.set(bytes.subarray(0, length));
    return bitmap;
  }

  /** Applies a set of verified ranges (as received in an `ack` message) onto this bitmap. */
  applyRanges(ranges: readonly BlockRange[]): void {
    for (const { start, end } of ranges) {
      for (let i = start; i <= end; i++) this.set(i);
    }
  }
}

function popcount(byte: number): number {
  let count = 0;
  let b = byte;
  while (b !== 0) {
    count += b & 1;
    b >>= 1;
  }
  return count;
}

/** Collapses a sorted or unsorted list of indices into inclusive contiguous ranges. */
export function indicesToRanges(indices: readonly number[]): BlockRange[] {
  if (indices.length === 0) return [];
  const sorted = [...indices].sort((a, b) => a - b);
  const ranges: BlockRange[] = [];
  let start = sorted[0];
  let end = sorted[0];

  for (let i = 1; i < sorted.length; i++) {
    const value = sorted[i];
    if (value === end + 1) {
      end = value;
    } else if (value !== end) {
      ranges.push({ start, end });
      start = value;
      end = value;
    }
  }
  ranges.push({ start, end });
  return ranges;
}

export function rangesToIndices(ranges: readonly BlockRange[]): number[] {
  const indices: number[] = [];
  for (const { start, end } of ranges) {
    for (let i = start; i <= end; i++) indices.push(i);
  }
  return indices;
}

function bytesToBase64(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

function base64ToBytes(base64: string): Uint8Array {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}
