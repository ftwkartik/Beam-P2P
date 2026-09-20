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
    WS_CLOSE_HELLO_TIMEOUT,
    WS_CLOSE_MALFORMED_MESSAGE,
    WS_CLOSE_MESSAGE_TOO_LARGE,
    WS_CLOSE_ORIGIN_NOT_ALLOWED,
    WS_CLOSE_RATE_LIMITED,
    WS_CLOSE_ROOM_NOT_FOUND,
    WS_CLOSE_SESSION_REPLACED,
    WS_CLOSE_UNAUTHORIZED,
)


def test_protocol_version_is_a_positive_int() -> None:
    assert isinstance(PROTOCOL_VERSION, int)
    assert PROTOCOL_VERSION >= 1


def test_frame_magic_is_two_bytes() -> None:
    assert FRAME_MAGIC == b"BM"
    assert len(FRAME_MAGIC) == 2


def test_frame_payload_size_fits_within_aiortcs_default_max_message_size() -> None:
    # aiortc's default SCTP maxMessageSize is exactly 65536; a full header+payload
    # frame must stay at or under that or a real browser peer refuses to send/deliver
    # it (see constants.py's DEFAULT_FRAME_PAYLOAD_SIZE docstring).
    assert DEFAULT_FRAME_PAYLOAD_SIZE + FRAME_HEADER_SIZE <= 65536


def test_block_size_is_not_smaller_than_one_frame() -> None:
    # docs/protocol.md §4.2: frames never cross block boundaries -- the last frame of
    # a block that isn't an exact multiple of the frame size is simply smaller, which
    # Frame/BlockMessage's construction already handles; this just checks the sizes
    # are sane relative to each other, not that they divide evenly.
    assert DEFAULT_BLOCK_SIZE >= DEFAULT_FRAME_PAYLOAD_SIZE


def test_size_limits_are_sane_and_ordered() -> None:
    assert 0 < MAX_ICE_CANDIDATE_BYTES < MAX_SDP_BYTES < MAX_SIGNALING_MESSAGE_BYTES
    assert FRAME_HEADER_SIZE == 16


def test_ws_close_codes_are_unique_and_in_the_application_range() -> None:
    codes = {
        WS_CLOSE_MALFORMED_MESSAGE,
        WS_CLOSE_UNAUTHORIZED,
        WS_CLOSE_ORIGIN_NOT_ALLOWED,
        WS_CLOSE_ROOM_NOT_FOUND,
        WS_CLOSE_HELLO_TIMEOUT,
        WS_CLOSE_SESSION_REPLACED,
        WS_CLOSE_MESSAGE_TOO_LARGE,
        WS_CLOSE_RATE_LIMITED,
    }
    assert len(codes) == 8
    # RFC 6455 reserves 3000-4999 for libraries/frameworks/applications; ours are
    # deliberately >= 4400 to stay clear of any framework's own codes in that range.
    assert all(4400 <= code <= 4999 for code in codes)
