"""Sanity checks for protocol constants (see docs/protocol.md for the pinned values)."""

from beam_protocol import PROTOCOL_VERSION
from beam_protocol.constants import (
    DEFAULT_BLOCK_SIZE,
    DEFAULT_FRAME_PAYLOAD_SIZE,
    FRAME_HEADER_SIZE,
    FRAME_MAGIC,
    MAX_ICE_CANDIDATE_BYTES,
    MAX_SDP_BYTES,
    MAX_SIGNALING_MESSAGE_BYTES,
)


def test_protocol_version_is_a_positive_int() -> None:
    assert isinstance(PROTOCOL_VERSION, int)
    assert PROTOCOL_VERSION >= 1


def test_frame_magic_is_two_bytes() -> None:
    assert FRAME_MAGIC == b"BM"
    assert len(FRAME_MAGIC) == 2


def test_block_size_is_a_multiple_of_frame_payload_size() -> None:
    # Frames must never cross block boundaries (docs/protocol.md §4.2).
    assert DEFAULT_BLOCK_SIZE % DEFAULT_FRAME_PAYLOAD_SIZE == 0


def test_size_limits_are_sane_and_ordered() -> None:
    assert 0 < MAX_ICE_CANDIDATE_BYTES < MAX_SDP_BYTES < MAX_SIGNALING_MESSAGE_BYTES
    assert FRAME_HEADER_SIZE == 16
