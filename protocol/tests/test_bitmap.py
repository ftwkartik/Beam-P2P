"""The verified-block bitmap (docs/protocol.md §4.1)."""

import pytest

from beam_protocol.bitmap import BlockBitmap, indices_to_ranges, ranges_to_indices
from beam_protocol.peer import BlockRange


def test_starts_with_no_bits_set() -> None:
    bitmap = BlockBitmap(10)
    assert bitmap.count() == 0
    assert not bitmap.is_complete()
    for i in range(10):
        assert bitmap.has(i) is False


def test_set_and_has() -> None:
    bitmap = BlockBitmap(4)
    bitmap.set(1)
    bitmap.set(3)
    assert bitmap.has(1) is True
    assert bitmap.has(3) is True
    assert bitmap.has(0) is False
    assert bitmap.has(2) is False
    assert bitmap.count() == 2


def test_is_complete_once_every_bit_is_set() -> None:
    bitmap = BlockBitmap(3)
    for i in range(3):
        bitmap.set(i)
    assert bitmap.is_complete()


def test_zero_block_count_is_immediately_complete() -> None:
    bitmap = BlockBitmap(0)
    assert bitmap.is_complete()
    assert bitmap.count() == 0


def test_negative_block_count_rejected() -> None:
    with pytest.raises(ValueError, match="block_count"):
        BlockBitmap(-1)


def test_out_of_range_index_rejected() -> None:
    bitmap = BlockBitmap(4)
    with pytest.raises(IndexError):
        bitmap.has(4)
    with pytest.raises(IndexError):
        bitmap.set(-1)


def test_base64_round_trip() -> None:
    bitmap = BlockBitmap(20)
    for i in (0, 5, 19):
        bitmap.set(i)
    restored = BlockBitmap.from_base64(bitmap.to_base64(), 20)
    assert restored.count() == 3
    for i in (0, 5, 19):
        assert restored.has(i)


def test_from_base64_of_empty_string_is_an_empty_bitmap() -> None:
    bitmap = BlockBitmap.from_base64("", 10)
    assert bitmap.count() == 0


def test_from_base64_truncates_to_the_target_block_count() -> None:
    # A bitmap saved for a 20-block file, loaded against a 4-block one (e.g. a file
    # that shrank): only the bytes that fit are used, never read out of bounds.
    saved = BlockBitmap(20)
    saved.set(0)
    saved.set(19)
    restored = BlockBitmap.from_base64(saved.to_base64(), 4)
    assert restored.block_count == 4
    assert restored.has(0)


def test_to_ranges_collapses_contiguous_runs() -> None:
    bitmap = BlockBitmap(10)
    for i in (0, 1, 2, 5, 6, 9):
        bitmap.set(i)
    assert bitmap.to_ranges() == [
        BlockRange(start=0, end=2),
        BlockRange(start=5, end=6),
        BlockRange(start=9, end=9),
    ]


def test_apply_ranges_sets_every_index_in_each_range() -> None:
    bitmap = BlockBitmap(10)
    bitmap.apply_ranges([BlockRange(start=2, end=4), BlockRange(start=7, end=7)])
    assert [i for i in range(10) if bitmap.has(i)] == [2, 3, 4, 7]


def test_indices_to_ranges_handles_unsorted_input() -> None:
    expected = [BlockRange(start=0, end=2), BlockRange(start=5, end=5)]
    assert indices_to_ranges([5, 1, 0, 2]) == expected


def test_indices_to_ranges_of_empty_list() -> None:
    assert indices_to_ranges([]) == []


def test_ranges_to_indices_is_the_inverse_of_indices_to_ranges() -> None:
    indices = [0, 1, 2, 5, 9, 10]
    assert ranges_to_indices(indices_to_ranges(indices)) == indices
