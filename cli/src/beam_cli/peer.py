"""WebRTC peer connection wrapper built on aiortc (ADR-008; docs/protocol.md §4).

Two important departures from the web client's `engine/peer.ts`, both because aiortc's
API shape genuinely differs from a browser's, not by choice:

- aiortc never emits `negotiationneeded` at all, so "perfect negotiation" (designed
  around browsers automatically firing it, possibly on both sides at once) doesn't
  directly apply. Negotiation is driven explicitly instead, and made glare-free by
  construction: only the impolite (room creator) side ever calls `create_offer()`; the
  polite (joiner) side only ever answers whatever offer arrives. A browser peer on the
  other end still runs its own full perfect-negotiation logic, but since the CLI side
  never independently initiates a competing offer, there is nothing for it to collide
  with.
- aiortc's `setLocalDescription()` blocks until ICE gathering is *complete* and embeds
  every candidate directly in the SDP (a plain, spec-compliant non-trickle offer/
  answer) rather than emitting them one at a time via an `icecandidate` event, which
  aiortc doesn't have either. The CLI therefore never sends its own `signal{kind:
  "candidate"}` messages -- it only ever needs to *receive* and apply a browser peer's
  trickled candidates via `add_ice_candidate()`.

aiortc also has no `restartIce()`: a genuine mid-session ICE failure can't be locally
recovered the way a browser's peer.ts does. Resuming after a real drop always goes
through a full reconnect (fresh signaling, fresh RTCPeerConnection) instead -- the same
path a browser reload uses (see resume.py).
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import cast

from aiortc import RTCConfiguration, RTCDataChannel, RTCPeerConnection, RTCSessionDescription
from aiortc import RTCIceServer as AiortcIceServer
from aiortc.sdp import candidate_from_sdp

from beam_cli.api import IceServer
from beam_protocol.signaling import IceSignal, SdpDescription, SdpSignal, SdpType, SignalData

#: Fixed IDs so both sides declare the same negotiated channels (docs/protocol.md §4).
CONTROL_CHANNEL_ID = 0
DATA_CHANNEL_ID = 1
#: docs/protocol.md §4.2: "resumes on bufferedamountlow (threshold 1 MiB)".
DATA_CHANNEL_LOW_WATERMARK_BYTES = 1024 * 1024

_FINGERPRINT_RE = re.compile(r"^a=fingerprint:(sha-256 [0-9A-Fa-f:]+)\r?$", re.MULTILINE)


def _extract_fingerprint(sdp: str) -> str | None:
    match = _FINGERPRINT_RE.search(sdp)
    return match.group(1) if match else None


def _strip_candidate_prefix(candidate: str) -> str:
    prefix = "candidate:"
    return candidate[len(prefix) :] if candidate.startswith(prefix) else candidate


@dataclass
class PeerConnection:
    """One WebRTC connection to the other peer. `polite` comes from the signaling
    `welcome` message, exactly as for the web client."""

    polite: bool
    ice_servers: list[IceServer]
    on_send_signal: Callable[[SignalData], Awaitable[None]]
    on_connected: Callable[[], None] | None = None
    #: `connectionstatechange` reaching "connected" does not imply the negotiated data
    #: channels have themselves reached "open" yet -- calling send() before that raises
    #: aiortc's InvalidStateError (the exact race M9 found in the browser too, where it
    #: throws instead). Wait for these, not on_connected, before sending anything.
    on_control_channel_open: Callable[[], None] | None = None
    on_data_channel_open: Callable[[], None] | None = None
    on_control_message: Callable[[str], None] | None = None
    on_data_frame: Callable[[bytes], None] | None = None

    pc: RTCPeerConnection = field(init=False, repr=False)
    control_channel: RTCDataChannel = field(init=False, repr=False)
    data_channel: RTCDataChannel = field(init=False, repr=False)

    def __post_init__(self) -> None:
        config = RTCConfiguration(
            iceServers=[
                AiortcIceServer(urls=s.urls, username=s.username, credential=s.credential)
                for s in self.ice_servers
            ]
        )
        self.pc = RTCPeerConnection(configuration=config)

        self.control_channel = self.pc.createDataChannel(
            "control", negotiated=True, id=CONTROL_CHANNEL_ID, ordered=True
        )
        self.data_channel = self.pc.createDataChannel(
            "data", negotiated=True, id=DATA_CHANNEL_ID, ordered=False
        )
        self.data_channel.bufferedAmountLowThreshold = DATA_CHANNEL_LOW_WATERMARK_BYTES

        @self.control_channel.on("open")
        def _on_control_open() -> None:
            if self.on_control_channel_open is not None:
                self.on_control_channel_open()

        @self.data_channel.on("open")
        def _on_data_open() -> None:
            if self.on_data_channel_open is not None:
                self.on_data_channel_open()

        @self.control_channel.on("message")
        def _on_control_message(message: object) -> None:
            if self.on_control_message is not None and isinstance(message, str):
                self.on_control_message(message)

        @self.data_channel.on("message")
        def _on_data_message(message: object) -> None:
            if self.on_data_frame is not None and isinstance(message, bytes):
                self.on_data_frame(message)

        @self.pc.on("connectionstatechange")
        def _on_connection_state_change() -> None:
            if self.pc.connectionState == "connected" and self.on_connected is not None:
                self.on_connected()

    async def start(self) -> None:
        """Call once both peers are known to be present. A no-op for the polite
        side -- see the module docstring on why only the impolite side offers."""
        if self.polite:
            return
        offer = await self.pc.createOffer()
        await self.pc.setLocalDescription(offer)
        await self._send_local_description()

    async def handle_signal(self, data: SignalData) -> None:
        if isinstance(data, SdpSignal):
            await self._handle_description(data.description)
        elif isinstance(data, IceSignal):
            await self._handle_candidate(data)

    async def _handle_description(self, description: SdpDescription) -> None:
        remote = RTCSessionDescription(sdp=description.sdp, type=description.type)
        await self.pc.setRemoteDescription(remote)
        if description.type == "offer":
            answer = await self.pc.createAnswer()
            await self.pc.setLocalDescription(answer)
            await self._send_local_description()

    async def _send_local_description(self) -> None:
        local = self.pc.localDescription
        assert local is not None  # set immediately above, always
        # aiortc only ever produces "offer" or "answer" from create_offer()/
        # create_answer(), but types it as a plain str; SdpType is a stricter Literal.
        assert local.type in ("offer", "answer", "pranswer", "rollback")
        sdp_type = cast(SdpType, local.type)
        description = SdpDescription(type=sdp_type, sdp=local.sdp)
        await self.on_send_signal(SdpSignal(description=description))

    async def _handle_candidate(self, signal: IceSignal) -> None:
        if signal.candidate is None:
            await self.pc.addIceCandidate(None)
            return
        candidate = candidate_from_sdp(_strip_candidate_prefix(signal.candidate.candidate))
        candidate.sdpMid = signal.candidate.sdpMid
        candidate.sdpMLineIndex = signal.candidate.sdpMLineIndex
        await self.pc.addIceCandidate(candidate)

    def get_fingerprints(self) -> tuple[str, str] | None:
        """Returns (local, remote) DTLS fingerprints for SAS derivation, or None until
        both descriptions are set."""
        local = self.pc.localDescription
        remote = self.pc.remoteDescription
        if local is None or remote is None:
            return None
        local_fp = _extract_fingerprint(local.sdp)
        remote_fp = _extract_fingerprint(remote.sdp)
        if local_fp is None or remote_fp is None:
            return None
        return local_fp, remote_fp

    async def close(self) -> None:
        await self.pc.close()
