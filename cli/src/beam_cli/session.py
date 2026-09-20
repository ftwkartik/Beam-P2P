"""Orchestrates one `beam send` or `beam receive` invocation: REST room lifecycle +
signaling + WebRTC peer connection + SAS derivation + the transfer engine, all wired
together. Direct counterpart of the web client's `engine/session.ts`, but linear
rather than a long-lived reactive state machine -- a CLI invocation does one thing and
exits, it doesn't need to survive multiple page-visible state transitions the way a
browser tab does.

Scope note: unlike session.ts, this does not automatically reconnect and resume after
a dropped connection (docs/protocol.md §3's "reconnects with its room token"). aiortc
already has no restartIce() (see peer.py), and building the reconnect-and-re-offer
orchestration on top of that is real additional work, deferred to a follow-up rather
than built partially -- a dropped `beam send`/`beam receive` reports the failure and
exits. storage.py's persisted bitmaps and receiver.py's was_accepted() marker exist
for when that reconnect wiring is added; this module just doesn't drive it yet.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from pathlib import Path

from pydantic import TypeAdapter

from beam_cli.api import ApiClient
from beam_cli.manifest import build_manifest
from beam_cli.peer import PeerConnection
from beam_cli.receiver import FileReadyResult, OfferedManifest, ReceiverProgress, TransferReceiver
from beam_cli.sender import SenderProgress, TransferSender
from beam_cli.signaling_client import SignalingCallbacks, SignalingClient
from beam_cli.storage import FilesystemStorage
from beam_protocol.peer import OfferFilesMessage, PeerMessage
from beam_protocol.sas import SasSymbol, derive_sas
from beam_protocol.signaling import SignalData, WelcomeMessage

_peer_message_adapter: TypeAdapter[PeerMessage] = TypeAdapter(PeerMessage)

TERMINAL_PHASES = ("completed", "failed", "cancelled", "declined")


async def _connect_and_pair(
    api: ApiClient,
    token: str,
    room_id: str,
    on_sas: Callable[[list[SasSymbol]], None],
) -> tuple[PeerConnection, SignalingClient]:
    """Joins the signaling room, waits for the other peer to be present (already
    there, or via `peer_joined`), and negotiates a WebRTC connection. Returns once
    connected and the SAS has been derived and reported via `on_sas`.
    """
    ice_servers = await api.get_ice_servers(room_id, token)

    welcome_box: dict[str, WelcomeMessage] = {}
    welcome_event = asyncio.Event()
    joined_event = asyncio.Event()

    def on_welcome(message: WelcomeMessage) -> None:
        welcome_box["w"] = message
        welcome_event.set()

    signaling = SignalingClient(
        ws_url=api.signaling_ws_url(),
        token=token,
        callbacks=SignalingCallbacks(
            on_welcome=on_welcome,
            on_peer_joined=lambda _m: joined_event.set(),
        ),
    )
    await signaling.connect()
    await welcome_event.wait()
    welcome = welcome_box["w"]

    if not welcome.peers:
        await joined_event.wait()

    peer_ready = asyncio.Event()
    #: `connectionstatechange` reaching "connected" does not imply the negotiated data
    #: channels have themselves reached "open" yet (see peer.py) -- callers must not
    #: send on either channel until both of these are set too.
    control_open = asyncio.Event()
    data_open = asyncio.Event()

    async def on_send_signal(data: SignalData) -> None:
        await signaling.send_signal(data)

    peer = PeerConnection(
        polite=welcome.polite,
        ice_servers=ice_servers,
        on_send_signal=on_send_signal,
        on_control_channel_open=control_open.set,
        on_data_channel_open=data_open.set,
    )

    def on_connected() -> None:
        fingerprints = peer.get_fingerprints()
        if fingerprints is None:
            return
        local_fp, remote_fp = fingerprints
        sas = list(derive_sas(room_id, local_fp, remote_fp))
        on_sas(sas)
        peer_ready.set()

    peer.on_connected = on_connected
    signaling.callbacks.on_signal = lambda _frm, data: peer.handle_signal(data)

    await peer.start()
    await asyncio.wait_for(peer_ready.wait(), timeout=60)
    await asyncio.wait_for(control_open.wait(), timeout=60)
    await asyncio.wait_for(data_open.wait(), timeout=60)
    return peer, signaling


async def run_send(
    paths: list[Path],
    base_url: str,
    *,
    on_room_ready: Callable[[str, str], None] | None = None,
    on_sas: Callable[[list[SasSymbol]], None] | None = None,
    on_phase_change: Callable[[str], None] | None = None,
    on_progress: Callable[[SenderProgress], None] | None = None,
    on_error: Callable[[str], None] | None = None,
) -> TransferSender:
    """Sends `paths` to whoever joins the room. Returns once the transfer reaches a
    terminal phase (completed/failed/cancelled/declined)."""
    entries = build_manifest(paths)
    api = ApiClient(base_url)
    created = await api.create_room()
    if on_room_ready is not None:
        on_room_ready(created.code, created.room_id)

    peer, signaling = await _connect_and_pair(
        api, created.token, created.room_id, on_sas or (lambda _s: None)
    )

    done = asyncio.Event()

    def handle_phase_change(phase: str) -> None:
        if on_phase_change is not None:
            on_phase_change(phase)
        if phase in TERMINAL_PHASES:
            done.set()

    sender = TransferSender(
        transfer_id=str(uuid.uuid4()),
        entries=entries,
        control=peer.control_channel,
        data=peer.data_channel,
        on_phase_change=handle_phase_change,
        on_progress=on_progress,
        on_error=on_error,
    )
    peer.on_control_message = lambda raw: sender.handle_control_message(
        _peer_message_adapter.validate_json(raw)
    )
    sender.start()

    await done.wait()
    await peer.close()
    await signaling.leave()
    return sender


async def run_receive(
    code: str,
    target_dir: Path,
    base_url: str,
    *,
    auto_accept: bool = False,
    on_sas: Callable[[list[SasSymbol]], None] | None = None,
    on_offer: Callable[[OfferedManifest], bool] | None = None,
    on_phase_change: Callable[[str], None] | None = None,
    on_progress: Callable[[ReceiverProgress], None] | None = None,
    on_file_ready: Callable[[FileReadyResult], None] | None = None,
    on_error: Callable[[str], None] | None = None,
) -> TransferReceiver:
    """Joins `code`'s room and receives whatever the other peer offers. `on_offer`,
    if given, is asked to return True (accept) or False (decline); ignored (offers
    are accepted immediately) when `auto_accept` is set.
    """
    api = ApiClient(base_url)
    joined = await api.join_room(code)
    peer, signaling = await _connect_and_pair(
        api, joined.token, joined.room_id, on_sas or (lambda _s: None)
    )

    done = asyncio.Event()

    def handle_phase_change(phase: str) -> None:
        if on_phase_change is not None:
            on_phase_change(phase)
        if phase in TERMINAL_PHASES:
            done.set()

    target_dir.mkdir(parents=True, exist_ok=True)
    receiver: TransferReceiver | None = None
    #: Holds a reference to the fire-and-forget accept() task so asyncio doesn't
    #: garbage-collect it mid-flight.
    background_tasks: set[asyncio.Task[None]] = set()

    def handle_offer(manifest: OfferedManifest) -> None:
        assert receiver is not None
        accepted = auto_accept or on_offer is None or on_offer(manifest)
        if accepted:
            task = asyncio.ensure_future(receiver.accept())
            background_tasks.add(task)
            task.add_done_callback(background_tasks.discard)
        else:
            receiver.decline("Declined by the recipient.")

    def route_control_message(raw: str) -> None:
        nonlocal receiver
        message = _peer_message_adapter.validate_json(raw)
        if receiver is None:
            # A sender always offers before anything else can meaningfully arrive
            # (docs/protocol.md §4.1); nothing else is a valid first message.
            if not isinstance(message, OfferFilesMessage):
                return
            storage = FilesystemStorage(target_dir, message.transfer_id)
            receiver = TransferReceiver(
                storage=storage,
                control=peer.control_channel,
                on_offer=handle_offer,
                on_phase_change=handle_phase_change,
                on_progress=on_progress,
                on_file_ready=on_file_ready,
                on_error=on_error,
            )
        receiver.handle_control_message(message)

    def route_data_frame(raw: bytes) -> None:
        if receiver is not None:
            receiver.handle_data_frame(raw)

    peer.on_control_message = route_control_message
    peer.on_data_frame = route_data_frame

    await done.wait()
    await peer.close()
    await signaling.leave()
    assert receiver is not None
    return receiver
