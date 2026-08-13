"""Short authentication string (SAS) derivation (see docs/protocol.md §4.4 and
docs/adr/009-sas-verification.md).

Both peers derive the same 5-symbol SAS from their room ID and the two DTLS
certificate fingerprints they actually negotiated with. If the signaling server
substituted fingerprints to sit in the middle of the connection, the two peers hold
different fingerprint pairs and compute different SAS values -- comparing them out of
band (reading them aloud, or side by side) is what catches that attack. See
docs/security.md §5 for why 40 bits (5 symbols from a 256-entry table) was chosen.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from beam_protocol.sas_table import SAS_TABLE, SAS_TABLE_SIZE

#: Domain separator, so a SAS can never collide with a hash used for something else
#: even if the same fingerprint pair were reused in another context.
_SAS_DOMAIN = b"beam-sas-v1"

#: Number of symbols in a rendered SAS. 5 symbols * 8 bits/symbol = 40 bits.
SAS_SYMBOL_COUNT = 5

assert SAS_TABLE_SIZE == 256, "the SAS table must have exactly 256 entries (8 bits/symbol)"

_FINGERPRINT_RE = re.compile(r"^sha-256 ([0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){31})$")


class InvalidFingerprintError(ValueError):
    """Raised when a string doesn't look like a `sha-256 AB:CD:...` DTLS fingerprint."""


@dataclass(frozen=True, slots=True)
class SasSymbol:
    """One rendered SAS symbol."""

    emoji: str
    name: str

    def __str__(self) -> str:
        return f"{self.emoji} {self.name}"


def normalize_fingerprint(fingerprint: str) -> str:
    """Validate and uppercase a `sha-256 AB:CD:...` DTLS fingerprint from an SDP.

    Raises `InvalidFingerprintError` if the string isn't a well-formed SHA-256
    fingerprint (32 colon-separated hex octets), so a malformed value fails loudly
    instead of silently producing a meaningless SAS.
    """
    candidate = fingerprint.strip()
    match = _FINGERPRINT_RE.match(candidate)
    if not match:
        raise InvalidFingerprintError(
            "expected a 'sha-256 AB:CD:...' fingerprint with 32 hex octets"
        )
    scheme, hex_part = candidate.split(" ", 1)
    return f"{scheme} {hex_part.upper()}"


def derive_sas(room_id: str, fingerprint_a: str, fingerprint_b: str) -> tuple[SasSymbol, ...]:
    """Derive the 5-symbol SAS for a room from both sides' DTLS fingerprints.

    `fingerprint_a` and `fingerprint_b` may be passed in either order: they are sorted
    before hashing so both peers (who each know "mine" and "theirs", not "A" and "B")
    compute the identical input and therefore the identical SAS.
    """
    fp_a = normalize_fingerprint(fingerprint_a)
    fp_b = normalize_fingerprint(fingerprint_b)
    ordered = sorted((fp_a, fp_b))

    message = _SAS_DOMAIN + room_id.encode("utf-8") + ordered[0].encode() + ordered[1].encode()
    digest = hashlib.sha256(message).digest()

    symbols = []
    for i in range(SAS_SYMBOL_COUNT):
        index = digest[i]  # one byte = 8 bits = one index into the 256-entry table
        emoji, name = SAS_TABLE[index]
        symbols.append(SasSymbol(emoji=emoji, name=name))
    return tuple(symbols)


def format_sas(symbols: tuple[SasSymbol, ...]) -> str:
    """Render a SAS as a single space-separated line for display or logging."""
    return "  ".join(str(s) for s in symbols)
