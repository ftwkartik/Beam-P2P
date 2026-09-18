"""Builds an `offer_files` manifest from real filesystem paths (docs/protocol.md
§4.1). Direct counterpart of the web client's `engine/transfer/manifest.ts`, adapted
for a real filesystem instead of a browser's `File[]`.
"""

from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from pathlib import Path

from beam_protocol.peer import (
    MAX_FILE_SIZE,
    MAX_FILES_PER_TRANSFER,
    FileOffer,
    PathSafetyError,
    validate_relative_path,
)


class ManifestError(ValueError):
    """Raised when the given paths can't be turned into a valid manifest."""


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    """One file to send: its wire manifest entry, plus the real path to read bytes
    from (never sent to the peer -- only `offer.path`, a name-only relative path, is)."""

    offer: FileOffer
    source_path: Path


def build_manifest(paths: list[Path]) -> list[ManifestEntry]:
    """Walks `paths` (files and/or directories, as given on the command line) into a
    flat, ordered list of manifest entries.

    A directory contributes every regular file beneath it, with paths made relative to
    the directory's *parent* -- so the directory's own name becomes the first path
    segment, the same way a browser's `webkitdirectory` picker reports paths (verified
    directly in Milestone 9's E2E suite). A bare file contributes just itself, under
    its own filename.
    """
    entries: list[ManifestEntry] = []
    seen_paths: set[str] = set()
    index = 0

    for input_path in paths:
        resolved = input_path.expanduser().resolve()
        if not resolved.exists():
            raise ManifestError(f"{input_path} does not exist")

        if resolved.is_dir():
            base = resolved.parent
            for file_path in sorted(p for p in resolved.rglob("*") if p.is_file()):
                rel_path = file_path.relative_to(base).as_posix()
                entries.append(_make_entry(index, rel_path, file_path, seen_paths))
                index += 1
        elif resolved.is_file():
            entries.append(_make_entry(index, resolved.name, resolved, seen_paths))
            index += 1
        else:
            raise ManifestError(f"{input_path} is neither a regular file nor a directory")

    if not entries:
        raise ManifestError("no files to send")
    if len(entries) > MAX_FILES_PER_TRANSFER:
        raise ManifestError(
            f"too many files: {len(entries)} exceeds the limit of {MAX_FILES_PER_TRANSFER}"
        )
    return entries


def _make_entry(index: int, rel_path: str, file_path: Path, seen_paths: set[str]) -> ManifestEntry:
    try:
        validate_relative_path(rel_path)
    except PathSafetyError as exc:
        raise ManifestError(f"unsafe path {rel_path!r}: {exc}") from exc
    if rel_path in seen_paths:
        raise ManifestError(f"duplicate path in manifest: {rel_path!r}")
    seen_paths.add(rel_path)

    stat = file_path.stat()
    if stat.st_size > MAX_FILE_SIZE:
        raise ManifestError(f"{rel_path} is too large ({stat.st_size} bytes)")

    mime, _ = mimetypes.guess_type(file_path.name)
    offer = FileOffer(
        index=index,
        path=rel_path,
        size=stat.st_size,
        mime=mime or "application/octet-stream",
        mtime=stat.st_mtime,
    )
    return ManifestEntry(offer=offer, source_path=file_path)
