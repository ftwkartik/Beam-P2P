"""Protocol-wide constants.

These values are pinned by docs/protocol.md. Changing any of them is a protocol
version bump (see PROTOCOL_VERSION), not a config change, because both peers and the
server must agree on them independently of runtime settings.
"""

#: Major signaling/peer protocol version. Bumped only for breaking wire changes.
#: Sent by every client in the `hello` message; a mismatch closes the connection.
PROTOCOL_VERSION: int = 1

#: Maximum size, in bytes, of a single WebSocket signaling message.
MAX_SIGNALING_MESSAGE_BYTES: int = 64 * 1024

#: Maximum size, in bytes, of an SDP body carried in a `signal` message.
MAX_SDP_BYTES: int = 32 * 1024

#: Maximum size, in bytes, of a single ICE candidate payload.
MAX_ICE_CANDIDATE_BYTES: int = 1024

#: Default size, in bytes, of one transfer block (see docs/protocol.md §4).
DEFAULT_BLOCK_SIZE: int = 1024 * 1024

#: Default payload size, in bytes, of one binary data-channel frame.
DEFAULT_FRAME_PAYLOAD_SIZE: int = 64 * 1024

#: Size, in bytes, of the binary frame header (magic, version, flags, file index, offset).
FRAME_HEADER_SIZE: int = 16

#: Magic bytes identifying a Beam binary frame ("BM").
FRAME_MAGIC: bytes = b"\x42\x4d"

#: Binary frame format version, carried in every frame header.
FRAME_VERSION: int = 1

# --- Signaling WebSocket close codes (docs/protocol.md §3, "Close codes") ----------
# 1000-2999 are reserved by RFC 6455; application close codes start at 4000.

WS_CLOSE_MALFORMED_MESSAGE: int = 4400
WS_CLOSE_UNAUTHORIZED: int = 4401
WS_CLOSE_ORIGIN_NOT_ALLOWED: int = 4403
WS_CLOSE_ROOM_NOT_FOUND: int = 4404
WS_CLOSE_HELLO_TIMEOUT: int = 4408
#: The same peer ID connected again; this (now stale) connection is being replaced.
WS_CLOSE_SESSION_REPLACED: int = 4409
WS_CLOSE_MESSAGE_TOO_LARGE: int = 4413
WS_CLOSE_RATE_LIMITED: int = 4429
