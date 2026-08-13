"""Short authentication string derivation (docs/protocol.md §4.4, ADR-009)."""

import pytest

from beam_protocol.sas import (
    SAS_SYMBOL_COUNT,
    InvalidFingerprintError,
    derive_sas,
    format_sas,
    normalize_fingerprint,
)

FP_A = "sha-256 " + ":".join(["AB"] * 32)
FP_B = "sha-256 " + ":".join(["CD"] * 32)


def test_derive_sas_returns_the_right_number_of_symbols() -> None:
    symbols = derive_sas("room-1", FP_A, FP_B)
    assert len(symbols) == SAS_SYMBOL_COUNT


def test_derive_sas_is_order_independent() -> None:
    """Both peers know 'mine' and 'theirs', not consistently 'A' and 'B'."""
    forward = derive_sas("room-1", FP_A, FP_B)
    backward = derive_sas("room-1", FP_B, FP_A)
    assert forward == backward


def test_derive_sas_is_deterministic() -> None:
    assert derive_sas("room-1", FP_A, FP_B) == derive_sas("room-1", FP_A, FP_B)


def test_derive_sas_differs_for_different_rooms() -> None:
    assert derive_sas("room-1", FP_A, FP_B) != derive_sas("room-2", FP_A, FP_B)


def test_derive_sas_differs_for_different_fingerprints() -> None:
    other_fp = "sha-256 " + ":".join(["EF"] * 32)
    assert derive_sas("room-1", FP_A, FP_B) != derive_sas("room-1", FP_A, other_fp)


def test_derive_sas_detects_a_substituted_fingerprint() -> None:
    """The whole point of the SAS: a MITM holding different certs on each side
    produces a different SAS for each peer (docs/security.md §5)."""
    attacker_fp = "sha-256 " + ":".join(["99"] * 32)
    alice_view = derive_sas("room-1", FP_A, attacker_fp)  # attacker's cert toward Alice
    bob_view = derive_sas("room-1", FP_B, attacker_fp)  # attacker's cert toward Bob (different)
    assert alice_view != bob_view


def test_normalize_fingerprint_uppercases_hex() -> None:
    lower = "sha-256 " + ":".join(["ab"] * 32)
    assert normalize_fingerprint(lower) == FP_A


def test_normalize_fingerprint_rejects_malformed_input() -> None:
    with pytest.raises(InvalidFingerprintError):
        normalize_fingerprint("not-a-fingerprint")


def test_normalize_fingerprint_rejects_wrong_octet_count() -> None:
    with pytest.raises(InvalidFingerprintError):
        normalize_fingerprint("sha-256 " + ":".join(["AB"] * 31))


def test_format_sas_includes_every_symbol() -> None:
    symbols = derive_sas("room-1", FP_A, FP_B)
    rendered = format_sas(symbols)
    for symbol in symbols:
        assert symbol.emoji in rendered
        assert symbol.name in rendered
