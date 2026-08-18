"""Client IP resolution for rate limiting (see docs/security.md §2).

`X-Forwarded-For` is only honored when the immediate connecting peer (the socket
address FastAPI/Starlette sees) is a configured trusted proxy. Otherwise it is
attacker-controlled and would let anyone bypass IP-based rate limits by sending a
fabricated header.
"""

from __future__ import annotations

from fastapi import Request


def get_client_ip(request: Request, trusted_proxies: list[str]) -> str:
    """Return the best-effort real client IP for the given request."""
    direct_ip = request.client.host if request.client else "unknown"

    if direct_ip not in trusted_proxies:
        return direct_ip

    forwarded_for = request.headers.get("x-forwarded-for")
    if not forwarded_for:
        return direct_ip

    # The header is a comma-separated chain, left-to-right from original client to
    # nearest proxy. The first entry is the original client -- but only trustworthy
    # because we already confirmed the *immediate* peer is a proxy we trust.
    first_hop = forwarded_for.split(",")[0].strip()
    return first_hop or direct_ip
