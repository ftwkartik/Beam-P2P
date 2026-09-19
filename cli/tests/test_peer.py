"""Two real aiortc peer connections, wired directly to each other (no signaling
server -- see docs/testing-strategy.md's "nothing extra, aiortc in-process").
"""

from __future__ import annotations

import asyncio

import pytest

from beam_cli.peer import PeerConnection
from beam_protocol.signaling import SignalData


async def _noop_signal(_data: SignalData) -> None:
    pass


def _pair() -> tuple[PeerConnection, PeerConnection]:
    creator = PeerConnection(polite=False, ice_servers=[], on_send_signal=_noop_signal)
    joiner = PeerConnection(polite=True, ice_servers=[], on_send_signal=_noop_signal)
    return creator, joiner


def _link(a: PeerConnection, b: PeerConnection) -> None:
    async def a_to_b(data: SignalData) -> None:
        await b.handle_signal(data)

    async def b_to_a(data: SignalData) -> None:
        await a.handle_signal(data)

    a.on_send_signal = a_to_b
    b.on_send_signal = b_to_a


async def _connect(creator: PeerConnection, joiner: PeerConnection) -> None:
    connected = {"creator": asyncio.Event(), "joiner": asyncio.Event()}
    creator.on_connected = connected["creator"].set
    joiner.on_connected = connected["joiner"].set

    await creator.start()  # only the impolite side offers
    await asyncio.wait_for(
        asyncio.gather(connected["creator"].wait(), connected["joiner"].wait()), timeout=10
    )


async def test_data_channels_open_and_exchange_messages() -> None:
    creator, joiner = _pair()
    _link(creator, joiner)
    received: list[str] = []
    joiner.on_control_message = received.append
    control_open = asyncio.Event()
    creator.on_control_channel_open = control_open.set

    await _connect(creator, joiner)
    await asyncio.wait_for(control_open.wait(), timeout=10)

    creator.control_channel.send("hello from creator")
    await asyncio.sleep(0.2)
    assert received == ["hello from creator"]

    await creator.close()
    await joiner.close()


async def test_both_sides_derive_the_same_fingerprints_pair() -> None:
    creator, joiner = _pair()
    _link(creator, joiner)

    await _connect(creator, joiner)

    creator_fps = creator.get_fingerprints()
    joiner_fps = joiner.get_fingerprints()
    assert creator_fps is not None
    assert joiner_fps is not None
    # Each side's (local, remote) pair, sorted, must match the other's -- this is
    # exactly what derive_sas() does before hashing (docs/protocol.md §4.4).
    assert sorted(creator_fps) == sorted(joiner_fps)

    await creator.close()
    await joiner.close()


async def test_polite_side_never_offers() -> None:
    # A no-op call, not an error: only the impolite (creator) side ever offers (see
    # peer.py's module docstring on why aiortc doesn't need full perfect negotiation).
    _, joiner = _pair()
    sent: list[SignalData] = []

    async def record(data: SignalData) -> None:
        sent.append(data)

    joiner.on_send_signal = record
    await joiner.start()

    assert sent == []
    await joiner.close()


@pytest.mark.filterwarnings("ignore")
async def test_close_is_idempotent() -> None:
    _, joiner = _pair()
    await joiner.close()
    await joiner.close()
