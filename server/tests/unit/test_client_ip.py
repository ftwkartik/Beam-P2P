"""Trusted-proxy-aware client IP resolution (docs/security.md §2)."""

from __future__ import annotations

from unittest.mock import MagicMock

from beam_server.security.client_ip import get_client_ip


def _request(direct_ip: str, forwarded_for: str | None = None) -> MagicMock:
    request = MagicMock()
    request.client.host = direct_ip
    request.headers = {"x-forwarded-for": forwarded_for} if forwarded_for else {}
    return request


def test_untrusted_peer_ignores_forwarded_header() -> None:
    """A direct, non-proxy connection: the header is attacker-controlled, so it must
    never be trusted even if present."""
    request = _request("203.0.113.7", forwarded_for="1.2.3.4")
    assert get_client_ip(request, trusted_proxies=[]) == "203.0.113.7"


def test_trusted_proxy_forwarded_header_is_used() -> None:
    request = _request("10.0.0.1", forwarded_for="203.0.113.7")
    assert get_client_ip(request, trusted_proxies=["10.0.0.1"]) == "203.0.113.7"


def test_trusted_proxy_takes_the_first_hop_of_a_chain() -> None:
    request = _request("10.0.0.1", forwarded_for="203.0.113.7, 10.0.0.2, 10.0.0.1")
    assert get_client_ip(request, trusted_proxies=["10.0.0.1"]) == "203.0.113.7"


def test_trusted_proxy_without_header_falls_back_to_direct_ip() -> None:
    request = _request("10.0.0.1")
    assert get_client_ip(request, trusted_proxies=["10.0.0.1"]) == "10.0.0.1"


def test_no_client_info_returns_unknown() -> None:
    request = MagicMock()
    request.client = None
    request.headers = {}
    assert get_client_ip(request, trusted_proxies=[]) == "unknown"
