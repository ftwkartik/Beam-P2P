"""Room code generation, formatting and normalization (docs/protocol.md §1)."""

import pytest

from beam_protocol.codes import (
    MAX_NAMEPLATE,
    MIN_NAMEPLATE,
    InvalidCodeError,
    RoomCode,
    format_code,
    generate_words,
    normalize_code,
    suggest_completions,
)
from beam_protocol.wordlist import WORDLIST


def test_generate_words_returns_words_from_the_list() -> None:
    words = generate_words()
    assert len(words) == 3
    assert all(w in WORDLIST for w in words)


def test_generate_words_count_is_configurable() -> None:
    assert len(generate_words(count=5)) == 5


def test_format_code_shape() -> None:
    assert format_code(7, ("otter", "lantern", "tiger")) == "7-otter-lantern-tiger"


def test_format_code_rejects_out_of_range_nameplate() -> None:
    with pytest.raises(ValueError, match="nameplate"):
        format_code(0, ("otter", "lantern", "tiger"))
    with pytest.raises(ValueError, match="nameplate"):
        format_code(MAX_NAMEPLATE + 1, ("otter", "lantern", "tiger"))


@pytest.mark.parametrize(
    "raw",
    [
        "7-otter-lantern-tiger",
        "7 otter lantern tiger",
        "  7-otter-lantern-tiger  ",
        "7-OTTER-LANTERN-TIGER",
        "7-Otter-Lantern-Tiger",
        "7 otter-lantern tiger",
    ],
)
def test_normalize_code_accepts_reasonable_formatting_variants(raw: str) -> None:
    code = normalize_code(raw)
    assert code == RoomCode(nameplate=7, words=("otter", "lantern", "tiger"))


def test_normalize_code_round_trips_through_str() -> None:
    code = normalize_code("7-otter-lantern-tiger")
    assert str(code) == "7-otter-lantern-tiger"


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "otter-lantern-tiger",  # missing nameplate
        "7-otter-lantern",  # only two words
        "7-otter-lantern-tiger-extra",  # four words
        "7-otter-lantern-notarealword",
        "abc-otter-lantern-tiger",  # non-numeric nameplate
        "99999-otter-lantern-tiger",  # nameplate out of range
        "0-otter-lantern-tiger",  # nameplate out of range (below minimum)
    ],
)
def test_normalize_code_rejects_malformed_input(raw: str) -> None:
    with pytest.raises(InvalidCodeError):
        normalize_code(raw)


def test_normalize_code_error_never_echoes_which_word_was_wrong() -> None:
    # docs/security.md: the same generic message for "unknown room" and "wrong
    # code" prevents an attacker from learning anything from the error text.
    with pytest.raises(InvalidCodeError) as exc_info:
        normalize_code("7-otter-lantern-notarealword")
    assert "notarealword" not in str(exc_info.value)


def test_min_nameplate_is_accepted() -> None:
    code = normalize_code(f"{MIN_NAMEPLATE}-otter-lantern-tiger")
    assert code.nameplate == MIN_NAMEPLATE


def test_max_nameplate_is_accepted() -> None:
    code = normalize_code(f"{MAX_NAMEPLATE}-otter-lantern-tiger")
    assert code.nameplate == MAX_NAMEPLATE


def test_suggest_completions_returns_matching_prefixes() -> None:
    completions = suggest_completions("ott")
    assert "otter" in completions
    assert all(c.startswith("ott") for c in completions)


def test_suggest_completions_respects_limit() -> None:
    completions = suggest_completions("a", limit=3)
    assert len(completions) == 3


def test_suggest_completions_empty_prefix_returns_nothing() -> None:
    assert suggest_completions("") == []
