"""Peer protocol: JSON control messages exchanged between the two peers over the
`control` WebRTC data channel (see docs/protocol.md §4.1). Bulk data itself travels as
binary frames on the separate `data` channel (see frames.py), never as JSON.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from beam_protocol.constants import PROTOCOL_VERSION

#: Manifest limits (docs/security.md §4, "Peer-to-peer protocol safety").
MAX_FILES_PER_TRANSFER = 10_000
MAX_PATH_SEGMENT_BYTES = 255
MAX_PATH_TOTAL_BYTES = 4096
#: JavaScript's Number.MAX_SAFE_INTEGER; sizes above this can't round-trip through a
#: browser's JSON without precision loss.
MAX_FILE_SIZE = 2**53 - 1

_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_WINDOWS_RESERVED_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)


class _StrictModel(BaseModel):
    """Base for every control message: unknown fields are rejected, not ignored."""

    model_config = ConfigDict(extra="forbid")


class PathSafetyError(ValueError):
    """Raised when a peer-supplied relative path fails safety validation."""


def validate_relative_path(path: str) -> str:
    """Validate a manifest entry's path is a safe, relative, forward-slash path.

    This is the one piece of manifest validation worth a standalone function: it's
    exercised directly by the path-safety test table (docs/testing-strategy.md) and
    reused again by the receiver's actual filesystem/OPFS join logic in Milestone 8-10,
    which must still re-check the *resolved* path stays inside the target directory --
    this function only rejects the input shapes that make that check meaningless.
    """
    if not path or path != path.strip():
        raise PathSafetyError("path must be non-empty with no leading/trailing whitespace")
    if len(path.encode("utf-8")) > MAX_PATH_TOTAL_BYTES:
        raise PathSafetyError(f"path exceeds {MAX_PATH_TOTAL_BYTES} bytes")
    if "\\" in path:
        raise PathSafetyError("path must use forward slashes")
    if path.startswith("/") or path.startswith("~"):
        raise PathSafetyError("path must be relative")
    if re.search(r"[\x00-\x1f]", path):
        raise PathSafetyError("path contains control characters")
    if len(path) >= 2 and path[1] == ":":
        raise PathSafetyError("path must not contain a drive letter")

    segments = path.split("/")
    for segment in segments:
        if segment in ("", ".", ".."):
            raise PathSafetyError("path must not contain empty, '.' or '..' segments")
        if len(segment.encode("utf-8")) > MAX_PATH_SEGMENT_BYTES:
            raise PathSafetyError(f"path segment exceeds {MAX_PATH_SEGMENT_BYTES} bytes")
        bare_name = segment.split(".", 1)[0].upper()
        if bare_name in _WINDOWS_RESERVED_NAMES:
            raise PathSafetyError(f"path segment '{segment}' is a reserved name on Windows")

    return path


class FileOffer(_StrictModel):
    """One file within an `offer_files` manifest."""

    index: int = Field(ge=0)
    path: str
    size: int = Field(ge=0, le=MAX_FILE_SIZE)
    mime: str
    mtime: float | None = None

    @field_validator("path")
    @classmethod
    def _validate_path(cls, value: str) -> str:
        return validate_relative_path(value)


class OfferFilesMessage(_StrictModel):
    type: Literal["offer_files"] = "offer_files"
    transfer_id: str
    block_size: int = Field(gt=0)
    files: list[FileOffer] = Field(max_length=MAX_FILES_PER_TRANSFER)

    @field_validator("files")
    @classmethod
    def _validate_unique_indices_and_paths(cls, files: list[FileOffer]) -> list[FileOffer]:
        indices = [f.index for f in files]
        if len(set(indices)) != len(indices):
            raise ValueError("file indices must be unique within a transfer")
        paths = [f.path for f in files]
        if len(set(paths)) != len(paths):
            raise ValueError("file paths must be unique within a transfer")
        return files


class AcceptMessage(_StrictModel):
    type: Literal["accept"] = "accept"
    transfer_id: str
    #: file index (as a string, since it travels through a JSON object key) -> a
    #: base64-encoded verified-block bitmap, empty on a first-time transfer.
    have: dict[str, str] = Field(default_factory=dict)


class DeclineMessage(_StrictModel):
    type: Literal["decline"] = "decline"
    transfer_id: str
    reason: str


class BlockMessage(_StrictModel):
    """Announces a block's hash before its frames are sent (docs/protocol.md §4.3)."""

    type: Literal["block"] = "block"
    file: int = Field(ge=0)
    index: int = Field(ge=0)
    sha256: str

    @field_validator("sha256")
    @classmethod
    def _validate_hex_digest(cls, value: str) -> str:
        if not _SHA256_HEX_RE.match(value):
            raise ValueError("sha256 must be 64 lowercase hex characters")
        return value


class BlockRange(_StrictModel):
    """An inclusive range of verified block indices, used to batch `ack` messages."""

    start: int = Field(ge=0)
    end: int = Field(ge=0)

    @model_validator(mode="after")
    def _end_not_before_start(self) -> BlockRange:
        if self.end < self.start:
            raise ValueError(f"range end ({self.end}) must be >= start ({self.start})")
        return self


class AckMessage(_StrictModel):
    type: Literal["ack"] = "ack"
    file: int = Field(ge=0)
    verified: list[BlockRange]


class NackMessage(_StrictModel):
    type: Literal["nack"] = "nack"
    file: int = Field(ge=0)
    index: int = Field(ge=0)
    reason: Literal["hash_mismatch"] = "hash_mismatch"


class FileDoneMessage(_StrictModel):
    """Announces the file root hash (over all block hashes; see docs/protocol.md §4.3)."""

    type: Literal["file_done"] = "file_done"
    file: int = Field(ge=0)
    sha256: str

    @field_validator("sha256")
    @classmethod
    def _validate_hex_digest(cls, value: str) -> str:
        if not _SHA256_HEX_RE.match(value):
            raise ValueError("sha256 must be 64 lowercase hex characters")
        return value


class FileVerifiedMessage(_StrictModel):
    type: Literal["file_verified"] = "file_verified"
    file: int = Field(ge=0)
    ok: bool


class TransferDoneMessage(_StrictModel):
    type: Literal["transfer_done"] = "transfer_done"
    transfer_id: str


class CancelMessage(_StrictModel):
    type: Literal["cancel"] = "cancel"
    transfer_id: str
    reason: str


class PeerErrorMessage(_StrictModel):
    type: Literal["error"] = "error"
    code: str
    message: str


class PeerHelloMessage(_StrictModel):
    """The first message on the `control` channel once it opens."""

    type: Literal["hello"] = "hello"
    proto: int = PROTOCOL_VERSION
    app_version: str
    max_frame: int = Field(gt=0)
    caps: list[str] = Field(default_factory=list)


PeerMessage = Annotated[
    PeerHelloMessage
    | OfferFilesMessage
    | AcceptMessage
    | DeclineMessage
    | BlockMessage
    | AckMessage
    | NackMessage
    | FileDoneMessage
    | FileVerifiedMessage
    | TransferDoneMessage
    | CancelMessage
    | PeerErrorMessage,
    Field(discriminator="type"),
]
