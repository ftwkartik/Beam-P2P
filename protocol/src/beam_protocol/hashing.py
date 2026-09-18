"""Per-block and file-root integrity hashing (see docs/protocol.md §4.3).

A plain streaming SHA-256 of a file needs its bytes in order, but blocks arrive
unordered over the data channel and resume skips blocks entirely -- so both the root
hash and every intermediate check are built from per-block hashes instead, which can
be computed (and verified) in any order. This is the direct counterpart of the web
client's `engine/transfer/hashing.ts`; the same golden values are used by both to
confirm they agree byte for byte.
"""

from __future__ import annotations

import hashlib

#: Domain separator for the root hash, so it can never collide with a hash used for
#: something else even given the same block hashes.
_ROOT_HASH_DOMAIN = b"beam-file-v1"


def hash_block(data: bytes) -> bytes:
    """The raw 32-byte SHA-256 digest of one block's bytes."""
    return hashlib.sha256(data).digest()


def hash_block_hex(data: bytes) -> str:
    """`hash_block`, as a lowercase hex string (the wire format for `block`/`file_done`)."""
    return hash_block(data).hex()


def derive_file_root_hash(size: int, block_hashes: list[bytes]) -> str:
    """The file root hash: SHA-256("beam-file-v1" || size_u64_be || h_0 || ... || h_n).

    `block_hashes` must be ordered by block index and every one present -- a file is
    never marked complete with a gap (docs/protocol.md §4.3), so callers only call this
    once every block has been verified.
    """
    if size < 0:
        raise ValueError(f"size must be non-negative, got {size}")
    message = _ROOT_HASH_DOMAIN + size.to_bytes(8, "big") + b"".join(block_hashes)
    return hashlib.sha256(message).hexdigest()
