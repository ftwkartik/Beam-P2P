"""Peer control-channel message validation, especially manifest path safety
(docs/security.md §4, "Peer-to-peer protocol safety")."""

import pytest
from pydantic import ValidationError

from beam_protocol.peer import (
    AckMessage,
    BlockMessage,
    BlockRange,
    FileOffer,
    OfferFilesMessage,
    PathSafetyError,
    validate_relative_path,
)


class TestPathSafety:
    @pytest.mark.parametrize(
        "path",
        [
            "report.pdf",
            "folder/report.pdf",
            "a/b/c/d/report.pdf",
            "my photos/2024/beach.jpg",
            "résumé.pdf",
        ],
    )
    def test_valid_paths_are_accepted(self, path: str) -> None:
        assert validate_relative_path(path) == path

    @pytest.mark.parametrize(
        "path",
        [
            "../escape.txt",
            "a/../../b.txt",
            "/etc/passwd",
            "~/secrets.txt",
            r"C:\Windows\system32",
            r"folder\file.txt",
            "",
            "   ",
            "a/./b.txt",
            "a//b.txt",
            "file\x00.txt",
            "file\nname.txt",
            "CON",
            "con.txt",
            "LPT1",
            "a" * 300,  # exceeds a single path segment's byte limit
        ],
    )
    def test_unsafe_paths_are_rejected(self, path: str) -> None:
        with pytest.raises(PathSafetyError):
            validate_relative_path(path)

    def test_total_path_length_is_capped(self) -> None:
        long_path = "/".join(["a"] * 3000)  # 5999 bytes, over the 4096-byte total cap
        with pytest.raises(PathSafetyError):
            validate_relative_path(long_path)


class TestFileOfferAndManifest:
    def _offer(self, index: int, path: str = "a.txt", size: int = 100) -> dict:
        return {"index": index, "path": path, "size": size, "mime": "text/plain"}

    def test_valid_offer_is_accepted(self) -> None:
        offer = FileOffer.model_validate(self._offer(0))
        assert offer.path == "a.txt"

    def test_offer_rejects_unsafe_path(self) -> None:
        with pytest.raises(ValidationError):
            FileOffer.model_validate(self._offer(0, path="../escape.txt"))

    def test_offer_rejects_negative_size(self) -> None:
        with pytest.raises(ValidationError):
            FileOffer.model_validate(self._offer(0, size=-1))

    def test_manifest_rejects_duplicate_indices(self) -> None:
        with pytest.raises(ValidationError, match="unique"):
            OfferFilesMessage(
                transfer_id="t1",
                block_size=1024,
                files=[self._offer(0, "a.txt"), self._offer(0, "b.txt")],  # type: ignore[list-item]
            )

    def test_manifest_rejects_duplicate_paths(self) -> None:
        with pytest.raises(ValidationError, match="unique"):
            OfferFilesMessage(
                transfer_id="t1",
                block_size=1024,
                files=[self._offer(0, "same.txt"), self._offer(1, "same.txt")],  # type: ignore[list-item]
            )

    def test_manifest_accepts_distinct_files(self) -> None:
        msg = OfferFilesMessage(
            transfer_id="t1",
            block_size=1024,
            files=[self._offer(0, "a.txt"), self._offer(1, "b.txt")],  # type: ignore[list-item]
        )
        assert len(msg.files) == 2


class TestBlockAndHashes:
    def test_block_message_requires_valid_hex_digest(self) -> None:
        with pytest.raises(ValidationError, match="sha256"):
            BlockMessage(file=0, index=0, sha256="not-hex")

    def test_block_message_rejects_wrong_length_digest(self) -> None:
        with pytest.raises(ValidationError, match="sha256"):
            BlockMessage(file=0, index=0, sha256="ab" * 31)

    def test_block_message_accepts_valid_digest(self) -> None:
        digest = "a" * 64
        msg = BlockMessage(file=0, index=0, sha256=digest)
        assert msg.sha256 == digest

    def test_block_message_rejects_uppercase_hex(self) -> None:
        # The wire format is lowercase hex only (docs/protocol.md §4.3); accepting
        # mixed case here would let two different byte strings compare unequal.
        with pytest.raises(ValidationError, match="sha256"):
            BlockMessage(file=0, index=0, sha256="A" * 64)


class TestBlockRange:
    def test_valid_range(self) -> None:
        r = BlockRange(start=0, end=5)
        assert r.start == 0
        assert r.end == 5

    def test_single_block_range(self) -> None:
        r = BlockRange(start=3, end=3)
        assert r.start == r.end == 3

    def test_end_before_start_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match=">="):
            BlockRange(start=5, end=0)


def test_ack_message_batches_ranges() -> None:
    msg = AckMessage(file=0, verified=[{"start": 0, "end": 3}, {"start": 5, "end": 5}])  # type: ignore[list-item]
    assert len(msg.verified) == 2
    assert msg.verified[1].start == 5
