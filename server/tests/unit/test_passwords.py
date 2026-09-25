"""Password hashing (Milestone 11; docs/adr/010)."""

from __future__ import annotations

from beam_server.security.passwords import hash_password, verify_password

PASSWORD = "correct horse battery staple"  # pragma: allowlist secret -- test-only, not real


def test_correct_password_verifies() -> None:
    h = hash_password(PASSWORD)
    assert verify_password(password_hash=h, password=PASSWORD)


def test_wrong_password_does_not_verify() -> None:
    h = hash_password(PASSWORD)
    assert not verify_password(password_hash=h, password="wrong")  # pragma: allowlist secret


def test_hash_is_not_the_plaintext() -> None:
    h = hash_password(PASSWORD)
    assert PASSWORD not in h


def test_two_hashes_of_the_same_password_differ() -> None:
    # argon2id salts automatically -- a DB leak shouldn't reveal which two users
    # share a password just by comparing hashes.
    a = hash_password(PASSWORD)
    b = hash_password(PASSWORD)
    assert a != b
    assert verify_password(password_hash=a, password=PASSWORD)
    assert verify_password(password_hash=b, password=PASSWORD)
