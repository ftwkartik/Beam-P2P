"""Shared channel-shaped types for sender.py/receiver.py (mirrors the web client's
`engine/transfer/channels.ts`). A real aiortc `RTCDataChannel` already satisfies
`ControlChannelLike` structurally, so no adapter is needed at the call site.
"""

from __future__ import annotations


class ControlChannelLike:
    def send(self, data: str) -> None:  # pragma: no cover - structural only
        raise NotImplementedError
