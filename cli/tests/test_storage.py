"""Filesystem storage: safe path resolution, partial-file writes, resume state, and
finalize-by-move (ADR-008's ".beam-partial/" resume state)."""

from pathlib import Path

import pytest

from beam_cli.storage import FilesystemStorage, PersistedFileState, StoragePathError
from beam_protocol.peer import PathSafetyError


async def test_write_and_read_back_bytes_at_an_offset(tmp_path: Path) -> None:
    storage = FilesystemStorage(tmp_path, "t1")
    handle = await storage.open_file(0, 10)
    await handle.write_at(2, b"abc")
    assert await handle.read_range(0, 10) == b"\x00\x00abc\x00\x00\x00\x00\x00"
    await handle.close()


async def test_out_of_order_writes_reassemble_correctly(tmp_path: Path) -> None:
    storage = FilesystemStorage(tmp_path, "t1")
    handle = await storage.open_file(0, 6)
    await handle.write_at(3, b"def")
    await handle.write_at(0, b"abc")
    assert await handle.read_range(0, 6) == b"abcdef"
    await handle.close()


async def test_reopening_the_same_file_index_preserves_bytes(tmp_path: Path) -> None:
    storage = FilesystemStorage(tmp_path, "t1")
    handle = await storage.open_file(0, 4)
    await handle.write_at(0, b"ab")
    await handle.close()

    # A fresh handle for the same (transfer, file) -- as a reconnect would open --
    # finds the bytes already on disk rather than a zeroed buffer.
    reopened = await storage.open_file(0, 4)
    assert await reopened.read_range(0, 2) == b"ab"
    await reopened.close()


async def test_finalize_moves_the_partial_file_to_its_destination(tmp_path: Path) -> None:
    storage = FilesystemStorage(tmp_path, "t1")
    handle = await storage.open_file(0, 5)
    await handle.write_at(0, b"hello")

    destination = storage.destination_path("a.txt")
    result = await handle.finalize(destination)

    assert result == destination
    assert destination.read_bytes() == b"hello"
    assert not (tmp_path / ".beam-partial" / "t1" / "0.part").exists()


async def test_finalize_creates_nested_destination_directories(tmp_path: Path) -> None:
    storage = FilesystemStorage(tmp_path, "t1")
    handle = await storage.open_file(0, 1)
    await handle.write_at(0, b"x")

    destination = storage.destination_path("nested/dir/file.bin")
    await handle.finalize(destination)

    assert destination.read_bytes() == b"x"


def test_destination_path_rejects_dotdot_shapes(tmp_path: Path) -> None:
    # Caught by validate_relative_path's shape check, before path resolution even runs.
    storage = FilesystemStorage(tmp_path, "t1")
    with pytest.raises(PathSafetyError):
        storage.destination_path("../escape.txt")


def test_destination_path_rejects_absolute_paths(tmp_path: Path) -> None:
    storage = FilesystemStorage(tmp_path, "t1")
    with pytest.raises(PathSafetyError):
        storage.destination_path("/etc/passwd")


def test_destination_path_rejects_a_symlink_that_escapes_the_target_dir(tmp_path: Path) -> None:
    # A shape validate_relative_path can't catch: "link/file.txt" is a perfectly
    # ordinary-looking relative path, but the symlink makes it resolve outside
    # target_dir -- this is exactly what the post-resolution is_relative_to check
    # in storage.py's _safe_join exists for, on top of (not instead of) shape validation.
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (target_dir / "link").symlink_to(outside)

    storage = FilesystemStorage(target_dir, "t1")
    with pytest.raises(StoragePathError):
        storage.destination_path("link/file.txt")


def test_state_round_trips(tmp_path: Path) -> None:
    storage = FilesystemStorage(tmp_path, "t1")
    state = PersistedFileState(size=10, bitmap_base64="gA==", block_hashes_hex=["aa" * 32, None])

    assert storage.load_state(0) is None
    storage.save_state(0, state)
    loaded = storage.load_state(0)

    assert loaded == state


def test_load_state_of_unknown_file_is_none(tmp_path: Path) -> None:
    storage = FilesystemStorage(tmp_path, "t1")
    assert storage.load_state(5) is None


def test_clear_transfer_removes_partial_dir_but_not_finished_files(tmp_path: Path) -> None:
    storage = FilesystemStorage(tmp_path, "t1")
    storage.save_state(0, PersistedFileState(size=1, bitmap_base64="", block_hashes_hex=[None]))
    finished = storage.destination_path("done.txt")
    finished.write_text("already finalized")

    storage.clear_transfer()

    assert not (tmp_path / ".beam-partial" / "t1").exists()
    assert finished.exists()


def test_accepted_marker_round_trips(tmp_path: Path) -> None:
    storage = FilesystemStorage(tmp_path, "t1")
    assert storage.was_accepted() is False
    storage.mark_accepted()
    assert storage.was_accepted() is True


def test_clear_transfer_removes_the_accepted_marker_too(tmp_path: Path) -> None:
    storage = FilesystemStorage(tmp_path, "t1")
    storage.mark_accepted()
    storage.clear_transfer()
    assert storage.was_accepted() is False


def test_two_transfers_have_independent_partial_dirs(tmp_path: Path) -> None:
    a = FilesystemStorage(tmp_path, "transfer-a")
    b = FilesystemStorage(tmp_path, "transfer-b")
    a.save_state(0, PersistedFileState(size=1, bitmap_base64="", block_hashes_hex=[None]))

    assert a.load_state(0) is not None
    assert b.load_state(0) is None
