"""Shared channel-shaped types for sender.py/receiver.py (mirrors the web client's
`engine/transfer/channels.ts`). A real aiortc `RTCDataChannel` already satisfies
`ControlChannelLike` structurally (a `Protocol`, not a concrete base class -- that's
what makes the structural match work), so no adapter is needed at the call site.
"""

from __future__ import annotations

from typing import Protocol


class ControlChannelLike(Protocol):
    def send(self, data: str) -> None: ...
