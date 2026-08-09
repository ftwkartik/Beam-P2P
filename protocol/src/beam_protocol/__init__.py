"""Shared wire-protocol definitions for the Beam signaling server, CLI and web client.

This package is the single source of truth for every message that crosses a process
boundary in Beam: the signaling protocol (client <-> server, over WebSocket) and the
peer protocol (browser/CLI <-> browser/CLI, over WebRTC data channels).

Full message models, the binary frame codec, room-code handling and SAS derivation are
added in Milestone 2 (see docs/roadmap.md and docs/protocol.md). This module currently
exposes only the package version and the protocol version constant so the workspace
resolves end to end from Milestone 1 onward.
"""

from beam_protocol.constants import PROTOCOL_VERSION

__all__ = ["PROTOCOL_VERSION", "__version__"]

__version__ = "0.1.0"
