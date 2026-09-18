"""The receiver-held verified-block bitmap (docs/protocol.md §4.1, `accept.have` and
`ack.verified`; docs/adr/005-transfer-protocol.md's "resume state lives with the
receiver"). One bit per block, set once a block's hash has been verified. Direct
counterpart of the web client's `engine/transfer/bitmap.ts`.
"""

from __future__ import annotations

import base64
import math

from beam_protocol.peer import BlockRange


class BlockBitmap:
    """One bit per block, indexed 0..block_count-1."""

    def __init__(self, block_count: int) -> None:
        if block_count < 0:
            raise ValueError("block_count must be >= 0")
        self.block_count = block_count
        self._bits = bytearray(math.ceil(block_count / 8))

    def _check_index(self, index: int) -> None:
        if not (0 <= index < self.block_count):
            raise IndexError(f"block index {index} out of range [0, {self.block_count})")

    def has(self, index: int) -> bool:
        self._check_index(index)
        return (self._bits[index >> 3] & (1 << (index & 7))) != 0

    def set(self, index: int) -> None:
        self._check_index(index)
        self._bits[index >> 3] |= 1 << (index & 7)

    def count(self) -> int:
        return sum(byte.bit_count() for byte in self._bits)

    def is_complete(self) -> bool:
        return self.count() == self.block_count

    def to_ranges(self) -> list[BlockRange]:
        """All verified indices, as inclusive ranges, in ascending order."""
        indices = [i for i in range(self.block_count) if self.has(i)]
        return indices_to_ranges(indices)

    def to_base64(self) -> str:
        return base64.b64encode(bytes(self._bits)).decode("ascii")

    @classmethod
    def from_base64(cls, encoded: str, block_count: int) -> BlockBitmap:
        bitmap = cls(block_count)
        if not encoded:
            return bitmap
        decoded = base64.b64decode(encoded)
        length = min(len(decoded), len(bitmap._bits))
        bitmap._bits[:length] = decoded[:length]
        return bitmap

    def apply_ranges(self, ranges: list[BlockRange]) -> None:
        """Applies a set of verified ranges (as received in an `ack` message)."""
        for r in ranges:
            for i in range(r.start, r.end + 1):
                self.set(i)


def indices_to_ranges(indices: list[int]) -> list[BlockRange]:
    """Collapses a sorted or unsorted list of indices into inclusive contiguous ranges."""
    if not indices:
        return []
    sorted_indices = sorted(indices)
    ranges: list[BlockRange] = []
    start = end = sorted_indices[0]

    for value in sorted_indices[1:]:
        if value == end + 1:
            end = value
        elif value != end:
            ranges.append(BlockRange(start=start, end=end))
            start = end = value

    ranges.append(BlockRange(start=start, end=end))
    return ranges


def ranges_to_indices(ranges: list[BlockRange]) -> list[int]:
    return [i for r in ranges for i in range(r.start, r.end + 1)]
