"""Room code generation, formatting and normalization.

A code looks like `7-otter-lantern-tiger`: a public *nameplate* (a small integer that
identifies the room) followed by 3 secret words from `WORDLIST` (see docs/protocol.md
§1). Guessing resistance comes from a low attempt limit enforced server-side
(Milestone 3), not from a huge secret space — see docs/security.md §1 for the exact
math. This module only handles the code's shape: generating words, formatting a code,
and normalizing/parsing user input back into its parts.
"""

from __future__ import annotations

import secrets
import unicodedata
from dataclasses import dataclass

from beam_protocol.wordlist import WORDLIST

#: Number of secret words in a code.
WORDS_PER_CODE = 3

#: Valid range for the public nameplate. The server widens toward the top of this
#: range under contention (see docs/data-model.md, "Nameplate allocation").
MIN_NAMEPLATE = 1
MAX_NAMEPLATE = 9999

_WORDLIST_INDEX: dict[str, int] = {word: i for i, word in enumerate(WORDLIST)}


class InvalidCodeError(ValueError):
    """Raised when user-supplied input cannot be parsed as a room code."""


@dataclass(frozen=True, slots=True)
class RoomCode:
    """A parsed, canonical room code."""

    nameplate: int
    words: tuple[str, str, str]

    def __str__(self) -> str:
        return format_code(self.nameplate, self.words)


def generate_words(count: int = WORDS_PER_CODE) -> tuple[str, ...]:
    """Pick `count` words from the wordlist using a cryptographically secure RNG.

    Words are drawn independently (with replacement is possible in principle, but
    astronomically unlikely at this list size); each draw uses `secrets.choice`, which
    is what makes the result unpredictable rather than merely random-looking.
    """
    return tuple(secrets.choice(WORDLIST) for _ in range(count))


def format_code(nameplate: int, words: tuple[str, ...]) -> str:
    """Join a nameplate and words into the canonical `nameplate-word-word-word` form."""
    if not (MIN_NAMEPLATE <= nameplate <= MAX_NAMEPLATE):
        raise ValueError(f"nameplate {nameplate} is outside [{MIN_NAMEPLATE}, {MAX_NAMEPLATE}]")
    return "-".join([str(nameplate), *words])


def _fold(text: str) -> str:
    """Normalize a single token: Unicode-fold, lowercase, strip surrounding space."""
    # NFKC collapses common look-alike/compatibility characters (e.g. full-width
    # digits, some homoglyphs) to their canonical ASCII form before comparison.
    return unicodedata.normalize("NFKC", text).strip().casefold()


def normalize_code(raw: str) -> RoomCode:
    """Parse and validate user-typed input into a canonical `RoomCode`.

    Accepts the input with variable whitespace and separators (`-`, spaces, or a mix)
    between tokens, since a code is often read aloud or pasted from a share link with
    different formatting. Raises `InvalidCodeError` with a message safe to show a user
    (it never echoes back which word, if any, was wrong -- see docs/security.md §1 on
    not distinguishing "unknown room" from "wrong code").
    """
    folded = _fold(raw)
    tokens = [t for t in folded.replace("-", " ").split(" ") if t]

    if len(tokens) != 1 + WORDS_PER_CODE:
        raise InvalidCodeError("A room code has a number followed by three words.")

    nameplate_token, *word_tokens = tokens
    if not nameplate_token.isdigit():
        raise InvalidCodeError("A room code starts with a number.")

    nameplate = int(nameplate_token)
    if not (MIN_NAMEPLATE <= nameplate <= MAX_NAMEPLATE):
        raise InvalidCodeError("That room code's number looks wrong.")

    for word in word_tokens:
        if word not in _WORDLIST_INDEX:
            raise InvalidCodeError("That room code's words look wrong.")

    words = (word_tokens[0], word_tokens[1], word_tokens[2])
    return RoomCode(nameplate=nameplate, words=words)


def suggest_completions(partial: str, *, limit: int = 8) -> list[str]:
    """Return up to `limit` words starting with `partial`, for input autocompletion."""
    prefix = _fold(partial)
    if not prefix:
        return []
    return [word for word in WORDLIST if word.startswith(prefix)][:limit]
