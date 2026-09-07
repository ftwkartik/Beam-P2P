/**
 * Binary data-channel frame codec -- a TypeScript port of beam_protocol.frames.Frame
 * (docs/protocol.md §4.2). Every frame carries its own file index and byte offset, so
 * frames can arrive in any order over the unordered `data` channel.
 *
 *     offset  size  field
 *     0       2     magic (0x42 0x4D, "BM")
 *     2       1     version
 *     3       1     flags (bit 0 = last frame of this block)
 *     4       4     file index   (uint32, big-endian)
 *     8       8     byte offset  (uint64, big-endian)
 *     16      n     payload
 *
 * `offset` is a `bigint`, not a `number`: a real uint64 offset can exceed
 * Number.MAX_SAFE_INTEGER, and the shared golden vectors (tests/vectors/frames.json)
 * include exactly that case.
 */

export const FRAME_HEADER_SIZE = 16;

const FRAME_MAGIC_0 = 0x42;
const FRAME_MAGIC_1 = 0x4d;
const FRAME_VERSION = 1;

/** Set when this frame completes its block (docs/protocol.md §4.2). */
export const FLAG_LAST_FRAME_OF_BLOCK = 0b0000_0001;

const UINT32_MAX = 2 ** 32 - 1;
const UINT64_MAX = (1n << 64n) - 1n;

export class FrameError extends Error {}

export interface Frame {
  fileIndex: number;
  offset: bigint;
  payload: Uint8Array;
  lastOfBlock: boolean;
}

export function createFrame(
  fileIndex: number,
  offset: bigint,
  payload: Uint8Array,
  lastOfBlock = false,
): Frame {
  if (!Number.isInteger(fileIndex) || fileIndex < 0 || fileIndex > UINT32_MAX) {
    throw new FrameError(`file_index ${fileIndex} does not fit in a uint32`);
  }
  if (offset < 0n || offset > UINT64_MAX) {
    throw new FrameError(`offset ${offset} does not fit in a uint64`);
  }
  return { fileIndex, offset, payload, lastOfBlock };
}

export function packFrame(frame: Frame): Uint8Array {
  const buffer = new Uint8Array(FRAME_HEADER_SIZE + frame.payload.length);
  const view = new DataView(buffer.buffer);

  buffer[0] = FRAME_MAGIC_0;
  buffer[1] = FRAME_MAGIC_1;
  buffer[2] = FRAME_VERSION;
  buffer[3] = frame.lastOfBlock ? FLAG_LAST_FRAME_OF_BLOCK : 0;
  view.setUint32(4, frame.fileIndex, false);
  view.setBigUint64(8, frame.offset, false);
  buffer.set(frame.payload, FRAME_HEADER_SIZE);

  return buffer;
}

/** Parses a received message into a `Frame`. Throws `FrameError` if malformed. */
export function unpackFrame(data: Uint8Array): Frame {
  if (data.length < FRAME_HEADER_SIZE) {
    throw new FrameError(`frame is only ${data.length} bytes, need at least ${FRAME_HEADER_SIZE}`);
  }

  if (data[0] !== FRAME_MAGIC_0 || data[1] !== FRAME_MAGIC_1) {
    throw new FrameError(`bad magic bytes: ${data[0]},${data[1]}`);
  }

  const version = data[2];
  if (version !== FRAME_VERSION) {
    throw new FrameError(`unsupported frame version: ${version} (expected ${FRAME_VERSION})`);
  }

  const view = new DataView(data.buffer, data.byteOffset, data.byteLength);
  const flags = data[3];
  const fileIndex = view.getUint32(4, false);
  const offset = view.getBigUint64(8, false);
  const payload = data.slice(FRAME_HEADER_SIZE);

  return {
    fileIndex,
    offset,
    payload,
    lastOfBlock: (flags & FLAG_LAST_FRAME_OF_BLOCK) !== 0,
  };
}
