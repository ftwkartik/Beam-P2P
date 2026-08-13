"""The binary data-channel frame codec (see docs/protocol.md §4.2).

Every frame is self-describing (it carries the file index and byte offset it belongs
at), so frames can arrive in any order over the unordered `data` channel and the
receiver can place each one correctly without a separate index message. This is the
one piece of the wire format that isn't a Pydantic model: it's packed/unpacked bytes,
because it has to be cheap to build and parse for every 64 KiB chunk of every transfer.

    offset  size  field
    0       2     magic (b"BM")
    2       1     version
    3       1     flags (bit 0 = last frame of this block)
    4       4     file index   (uint32, big-endian)
    8       8     byte offset  (uint64, big-endian)
    16      n     payload
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from beam_protocol.constants import FRAME_HEADER_SIZE, FRAME_MAGIC, FRAME_VERSION

# ">" = network (big-endian) byte order, standard sizes, no padding.
_HEADER_STRUCT = struct.Struct(">2sBBIQ")
assert _HEADER_STRUCT.size == FRAME_HEADER_SIZE, (
    f"header struct size {_HEADER_STRUCT.size} != FRAME_HEADER_SIZE {FRAME_HEADER_SIZE}"
)

_UINT32_MAX = 2**32 - 1
_UINT64_MAX = 2**64 - 1

#: Set when this frame completes its block (docs/protocol.md §4.2).
FLAG_LAST_FRAME_OF_BLOCK = 0b0000_0001


class FrameError(ValueError):
    """Raised when bytes cannot be decoded as a valid Beam frame, or a field is out of range."""


@dataclass(frozen=True, slots=True)
class Frame:
    """One binary data-channel frame: a header plus a payload slice."""

    file_index: int
    offset: int
    payload: bytes
    last_of_block: bool = False

    def __post_init__(self) -> None:
        if not (0 <= self.file_index <= _UINT32_MAX):
            raise FrameError(f"file_index {self.file_index} does not fit in a uint32")
        if not (0 <= self.offset <= _UINT64_MAX):
            raise FrameError(f"offset {self.offset} does not fit in a uint64")

    def pack(self) -> bytes:
        """Serialize this frame to bytes, ready to send over the data channel."""
        flags = FLAG_LAST_FRAME_OF_BLOCK if self.last_of_block else 0
        header = _HEADER_STRUCT.pack(
            FRAME_MAGIC, FRAME_VERSION, flags, self.file_index, self.offset
        )
        return header + self.payload

    @classmethod
    def unpack(cls, data: bytes | bytearray) -> Frame:
        """Parse a received message into a `Frame`. Raises `FrameError` if malformed."""
        if len(data) < FRAME_HEADER_SIZE:
            raise FrameError(f"frame is only {len(data)} bytes, need at least {FRAME_HEADER_SIZE}")

        magic, version, flags, file_index, offset = _HEADER_STRUCT.unpack_from(data, 0)

        if magic != FRAME_MAGIC:
            raise FrameError(f"bad magic bytes: {magic!r}")
        if version != FRAME_VERSION:
            raise FrameError(f"unsupported frame version: {version} (expected {FRAME_VERSION})")

        payload = bytes(data[FRAME_HEADER_SIZE:])
        return cls(
            file_index=file_index,
            offset=offset,
            payload=payload,
            last_of_block=bool(flags & FLAG_LAST_FRAME_OF_BLOCK),
        )
