"""Per-block and file-root hashing (docs/protocol.md §4.3).

The digests below are pinned against web/src/engine/transfer/hashing.test.ts's values
(both independently derived from the same spec, then cross-checked byte for byte) --
this is what actually proves the two implementations agree, not just that each one is
internally consistent. Single-letter names keep each line under the length limit with
its `pragma: allowlist secret` comment (detect-secrets requires it inline, same line):
`_A`/`_B` = hash_block_hex of b"a"/b"hello world"; `_C`/`_D` = the root hash of size=13
over [hash(b"a"), hash(b"b")], and of size=0 over no blocks at all.
"""

import pytest

from beam_protocol.hashing import derive_file_root_hash, hash_block, hash_block_hex

_A = "ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb"  # pragma: allowlist secret
_B = "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"  # pragma: allowlist secret
_C = "c091ceaa9e2f4f242a40d2cf7bbdb4b3a13a157a9f26724b5e5e3e9ed9cf42e2"  # pragma: allowlist secret
_D = "c6bb6e55377c307e905cf1106121b75e7afec076c05cac943803beb1ca326909"  # pragma: allowlist secret


def test_hash_block_matches_plain_sha256() -> None:
    assert hash_block_hex(b"a") == _A


def test_hash_block_hex_is_lowercase_sha256_hex() -> None:
    digest = hash_block_hex(b"hello world")
    assert digest == _B
    assert digest == digest.lower()
    assert len(digest) == 64


def test_root_hash_of_two_blocks_matches_the_ts_golden_value() -> None:
    hash_a = hash_block(b"a")
    hash_b = hash_block(b"b")
    root = derive_file_root_hash(13, [hash_a, hash_b])
    assert root == _C


def test_root_hash_of_zero_blocks_matches_the_ts_golden_value() -> None:
    assert derive_file_root_hash(0, []) == _D


def test_root_hash_depends_on_block_order() -> None:
    hash_a = hash_block(b"a")
    hash_b = hash_block(b"b")
    assert derive_file_root_hash(2, [hash_a, hash_b]) != derive_file_root_hash(2, [hash_b, hash_a])


def test_root_hash_depends_on_size() -> None:
    hash_a = hash_block(b"a")
    assert derive_file_root_hash(1, [hash_a]) != derive_file_root_hash(2, [hash_a])


def test_root_hash_rejects_negative_size() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        derive_file_root_hash(-1, [])
