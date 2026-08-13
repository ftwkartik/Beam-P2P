"""Binary frame codec round-trip and validation (docs/protocol.md §4.2)."""

import pytest

from beam_protocol.constants import FRAME_HEADER_SIZE, FRAME_MAGIC
from beam_protocol.frames import FLAG_LAST_FRAME_OF_BLOCK, Frame, FrameError


def test_pack_unpack_round_trip() -> None:
    frame = Frame(file_index=3, offset=65536, payload=b"hello world", last_of_block=True)
    packed = frame.pack()
    assert len(packed) == FRAME_HEADER_SIZE + len(b"hello world")

    unpacked = Frame.unpack(packed)
    assert unpacked == frame


def test_pack_header_layout_matches_spec() -> None:
    frame = Frame(file_index=1, offset=0, payload=b"")
    packed = frame.pack()
    assert packed[0:2] == FRAME_MAGIC
    assert packed[2] == 1  # version
    assert packed[3] == 0  # flags: not last
    assert int.from_bytes(packed[4:8], "big") == 1  # file_index
    assert int.from_bytes(packed[8:16], "big") == 0  # offset


def test_last_of_block_flag_round_trips() -> None:
    not_last = Frame(file_index=0, offset=0, payload=b"x").pack()
    last = Frame(file_index=0, offset=0, payload=b"x", last_of_block=True).pack()
    assert not_last[3] & FLAG_LAST_FRAME_OF_BLOCK == 0
    assert last[3] & FLAG_LAST_FRAME_OF_BLOCK != 0
    assert Frame.unpack(last).last_of_block is True
    assert Frame.unpack(not_last).last_of_block is False


def test_empty_payload_is_valid() -> None:
    frame = Frame(file_index=0, offset=0, payload=b"")
    assert Frame.unpack(frame.pack()) == frame


def test_out_of_range_file_index_rejected() -> None:
    with pytest.raises(FrameError):
        Frame(file_index=2**32, offset=0, payload=b"")


def test_negative_file_index_rejected() -> None:
    with pytest.raises(FrameError):
        Frame(file_index=-1, offset=0, payload=b"")


def test_out_of_range_offset_rejected() -> None:
    with pytest.raises(FrameError):
        Frame(file_index=0, offset=2**64, payload=b"")


def test_unpack_too_short_raises() -> None:
    with pytest.raises(FrameError, match=r"too short|need at least"):
        Frame.unpack(b"\x00" * (FRAME_HEADER_SIZE - 1))


def test_unpack_bad_magic_raises() -> None:
    good = Frame(file_index=0, offset=0, payload=b"x").pack()
    corrupted = b"XX" + good[2:]
    with pytest.raises(FrameError, match="magic"):
        Frame.unpack(corrupted)


def test_unpack_bad_version_raises() -> None:
    good = bytearray(Frame(file_index=0, offset=0, payload=b"x").pack())
    good[2] = 99  # version byte
    with pytest.raises(FrameError, match="version"):
        Frame.unpack(bytes(good))


def test_frames_never_cross_block_boundary_by_construction() -> None:
    """Sanity check on the constants used to compute frames-per-block elsewhere."""
    from beam_protocol.constants import DEFAULT_BLOCK_SIZE, DEFAULT_FRAME_PAYLOAD_SIZE

    assert DEFAULT_BLOCK_SIZE % DEFAULT_FRAME_PAYLOAD_SIZE == 0
