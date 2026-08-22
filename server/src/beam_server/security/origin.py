"""Origin checking for the signaling WebSocket (docs/protocol.md §3, "Connection
lifecycle"; docs/security.md §2).

Browsers send an `Origin` header on WebSocket upgrade requests, which must match a
configured allowlist -- otherwise any web page could open a signaling connection using
the visitor's cookies/network position (cross-site WebSocket hijacking). Non-browser
clients (the CLI) send no Origin header at all and are authorized by their room token
instead.
"""

from __future__ import annotations


def is_origin_allowed(origin: str | None, allowed_origins: list[str]) -> bool:
    """True if the connection may proceed: no Origin header (a non-browser client),
    or an Origin header that matches the allowlist exactly.
    """
    if origin is None:
        return True
    return origin in allowed_origins
