"""Manifest building from real filesystem paths (docs/protocol.md §4.1)."""

from pathlib import Path

import pytest

from beam_cli.manifest import ManifestError, build_manifest


def test_a_single_file_uses_its_own_name_as_the_path(tmp_path: Path) -> None:
    file_path = tmp_path / "hello.txt"
    file_path.write_text("hi")

    entries = build_manifest([file_path])

    assert len(entries) == 1
    assert entries[0].offer.path == "hello.txt"
    assert entries[0].offer.size == 2
    assert entries[0].offer.index == 0
    assert entries[0].source_path == file_path


def test_a_directory_prefixes_paths_with_its_own_name(tmp_path: Path) -> None:
    folder = tmp_path / "photos"
    folder.mkdir()
    (folder / "top.jpg").write_bytes(b"x")
    nested = folder / "nested"
    nested.mkdir()
    (nested / "inner.jpg").write_bytes(b"yy")

    entries = build_manifest([folder])
    paths = sorted(e.offer.path for e in entries)

    assert paths == ["photos/nested/inner.jpg", "photos/top.jpg"]


def test_multiple_top_level_paths_are_all_included(tmp_path: Path) -> None:
    file_a = tmp_path / "a.txt"
    file_a.write_text("a")
    folder = tmp_path / "b"
    folder.mkdir()
    (folder / "c.txt").write_text("c")

    entries = build_manifest([file_a, folder])
    paths = sorted(e.offer.path for e in entries)

    assert paths == ["a.txt", "b/c.txt"]


def test_indices_are_assigned_in_order_starting_at_zero(tmp_path: Path) -> None:
    paths = []
    for name in ("a.txt", "b.txt", "c.txt"):
        p = tmp_path / name
        p.write_text(name)
        paths.append(p)

    entries = build_manifest(paths)
    assert [e.offer.index for e in entries] == [0, 1, 2]


def test_nonexistent_path_raises() -> None:
    with pytest.raises(ManifestError, match="does not exist"):
        build_manifest([Path("/no/such/path/exists/here")])


def test_no_paths_raises() -> None:
    with pytest.raises(ManifestError, match="no files"):
        build_manifest([])


def test_an_empty_directory_contributes_nothing_and_then_raises(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ManifestError, match="no files"):
        build_manifest([empty])


def test_two_bare_files_with_the_same_basename_collide(tmp_path: Path) -> None:
    dir_a = tmp_path / "dir_a"
    dir_b = tmp_path / "dir_b"
    dir_a.mkdir()
    dir_b.mkdir()
    file_a = dir_a / "same.txt"
    file_b = dir_b / "same.txt"
    file_a.write_text("a")
    file_b.write_text("b")

    with pytest.raises(ManifestError, match="duplicate path"):
        build_manifest([file_a, file_b])


def test_mime_type_is_guessed_from_the_extension(tmp_path: Path) -> None:
    file_path = tmp_path / "doc.pdf"
    file_path.write_bytes(b"%PDF-1.4")
    entries = build_manifest([file_path])
    assert entries[0].offer.mime == "application/pdf"


def test_unknown_extension_falls_back_to_octet_stream(tmp_path: Path) -> None:
    file_path = tmp_path / "mystery.beamweird"
    file_path.write_bytes(b"?")
    entries = build_manifest([file_path])
    assert entries[0].offer.mime == "application/octet-stream"
