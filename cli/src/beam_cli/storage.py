"""Safe filesystem storage for received files, with resume state in `.beam-partial/`
(ADR-008). Direct counterpart of the web client's `storage/opfs-storage.ts` plus
`transfer/resume-store.ts` combined into one: OPFS and IndexedDB are two different
browser storage areas, but on a real filesystem both partial bytes and their resume
metadata naturally live side by side.

Resume here covers a transfer surviving a reconnect *within one continuous `beam
receive` process* (the same case as the web client's "network drop" scenario): the
persisted bitmap is keyed by transfer id, which is stable across a reconnect because
the sender's own process keeps it in memory. It does not yet cover resuming after the
whole `beam receive` process is killed and re-run -- that needs the room token itself
persisted and re-used to skip rejoining, which `session.py` doesn't build in this
milestone (a documented follow-up, not an oversight).
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from beam_protocol.peer import validate_relative_path

PARTIAL_DIR_NAME = ".beam-partial"


class StoragePathError(ValueError):
    """A resolved path would escape the target directory."""


@dataclass(frozen=True, slots=True)
class PersistedFileState:
    size: int
    bitmap_base64: str
    #: One hex string per block, or None for a block not yet verified.
    block_hashes_hex: list[str | None]


def _safe_join(base: Path, relative_path: str) -> Path:
    """Joins `relative_path` under `base`, re-checking the *resolved* result actually
    stays inside `base` -- validate_relative_path only rejects input shapes that would
    make this check meaningless (its own docstring), it doesn't replace it."""
    validate_relative_path(relative_path)
    resolved = (base / relative_path).resolve()
    if not resolved.is_relative_to(base.resolve()):
        raise StoragePathError(f"{relative_path!r} resolves outside the target directory")
    return resolved


class FileStorageHandle:
    def __init__(self, partial_path: Path, size: int) -> None:
        self.size = size
        self._path = partial_path
        partial_path.parent.mkdir(parents=True, exist_ok=True)
        if not partial_path.exists():
            with partial_path.open("wb") as f:
                f.truncate(size)
        self._file = partial_path.open("r+b")

    async def write_at(self, offset: int, data: bytes) -> None:
        self._file.seek(offset)
        self._file.write(data)

    async def read_range(self, start: int, end: int) -> bytes:
        self._file.seek(start)
        return self._file.read(end - start)

    async def finalize(self, destination: Path) -> Path:
        """Closes the partial file and moves it to its final destination, creating
        parent directories as needed. Disk-backed and a single rename when possible
        (same filesystem) -- never reads the whole file into memory."""
        self._file.close()
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(self._path), str(destination))
        return destination

    async def close(self) -> None:
        if not self._file.closed:
            self._file.close()


class FilesystemStorage:
    """One instance per transfer. `target_dir` is where finished files land;
    partial bytes and resume state live under `target_dir/.beam-partial/<transfer_id>/`
    until each file completes.
    """

    def __init__(self, target_dir: Path, transfer_id: str) -> None:
        self.target_dir = target_dir.resolve()
        self.transfer_id = transfer_id
        self._partial_dir = self.target_dir / PARTIAL_DIR_NAME / transfer_id

    def destination_path(self, relative_path: str) -> Path:
        return _safe_join(self.target_dir, relative_path)

    def _partial_path(self, file_index: int) -> Path:
        return self._partial_dir / f"{file_index}.part"

    def _state_path(self, file_index: int) -> Path:
        return self._partial_dir / f"{file_index}.state.json"

    def _accepted_marker_path(self) -> Path:
        return self._partial_dir / "accepted"

    async def open_file(self, file_index: int, size: int) -> FileStorageHandle:
        return FileStorageHandle(self._partial_path(file_index), size)

    def was_accepted(self) -> bool:
        """Whether this transfer id was already accepted before -- lets a resumed
        `offer_files` (the sender re-offering after a reconnect, same transfer id)
        skip re-prompting the user for consent."""
        return self._accepted_marker_path().exists()

    def mark_accepted(self) -> None:
        marker = self._accepted_marker_path()
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()

    def load_state(self, file_index: int) -> PersistedFileState | None:
        path = self._state_path(file_index)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            return PersistedFileState(
                size=data["size"],
                bitmap_base64=data["bitmap_base64"],
                block_hashes_hex=data["block_hashes_hex"],
            )
        except (json.JSONDecodeError, KeyError, TypeError):
            return None

    def save_state(self, file_index: int, state: PersistedFileState) -> None:
        path = self._state_path(file_index)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "size": state.size,
                    "bitmap_base64": state.bitmap_base64,
                    "block_hashes_hex": state.block_hashes_hex,
                }
            )
        )

    def clear_transfer(self) -> None:
        """Removes every partial file and resume record for this transfer -- called
        once it completes, is declined or is cancelled."""
        if self._partial_dir.exists():
            shutil.rmtree(self._partial_dir)
