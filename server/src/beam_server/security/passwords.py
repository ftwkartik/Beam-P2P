"""Password hashing for accounts (Milestone 11; docs/adr/010-accounts-and-history.md).

argon2id via `argon2-cffi` -- the OWASP-recommended default, memory-hard against GPU
cracking. No hand-rolled hashing: this is the one place "crypto comes from standard
libraries only" (docs/security.md) extends to a well-known third-party binding, not
just the stdlib, because Python's stdlib has no password-hashing primitive at all.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(*, password_hash: str, password: str) -> bool:
    """True if `password` matches `password_hash`. Never raises on a wrong password --
    only a malformed hash (which means a DB integrity problem, not user input) would."""
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False
