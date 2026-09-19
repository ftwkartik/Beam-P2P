"""A real TransferSender <-> TransferReceiver round trip over two real aiortc peer
connections wired directly together (no signaling server -- see docs/testing-
strategy.md's "CLI sender <-> CLI receiver over loopback... nothing extra, aiortc
in-process"). Mirrors web/src/engine/transfer/integration.test.ts.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from pydantic import TypeAdapter

from beam_cli.manifest import build_manifest
from beam_cli.peer import PeerConnection
from beam_cli.receiver import FileReadyResult, TransferReceiver
from beam_cli.sender import TransferSender
from beam_cli.storage import FilesystemStorage
from beam_protocol.hashing import hash_block_hex
from beam_protocol.peer import PeerMessage
from beam_protocol.signaling import SignalData

_peer_message_adapter: TypeAdapter[PeerMessage] = TypeAdapter(PeerMessage)


def _random_bytes(size: int, seed: int) -> bytes:
    """A tiny xorshift PRNG: deterministic (reproducible test failures) and fast
    enough for multi-MB fixtures, unlike re-seeding a CSPRNG per byte."""
    out = bytearray(size)
    state = seed
    for i in range(size):
        state ^= (state << 13) & 0xFFFFFFFF
        state ^= state >> 17
        state ^= (state << 5) & 0xFFFFFFFF
        out[i] = state & 0xFF
    return bytes(out)


async def _noop_signal(_data: SignalData) -> None:
    pass


async def _connect(creator: PeerConnection, joiner: PeerConnection) -> None:
    async def creator_to_joiner(data: SignalData) -> None:
        await joiner.handle_signal(data)

    async def joiner_to_creator(data: SignalData) -> None:
        await creator.handle_signal(data)

    creator.on_send_signal = creator_to_joiner
    joiner.on_send_signal = joiner_to_creator

    control_open = {"creator": asyncio.Event(), "joiner": asyncio.Event()}
    creator.on_control_channel_open = control_open["creator"].set
    joiner.on_control_channel_open = control_open["joiner"].set

    await creator.start()
    await asyncio.wait_for(
        asyncio.gather(control_open["creator"].wait(), control_open["joiner"].wait()), timeout=10
    )


async def test_two_files_transfer_intact_at_real_default_sizes(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    file_a = src_dir / "a.bin"
    file_a.write_bytes(_random_bytes(3 * 1024 * 1024 + 12345, 0x1234))
    file_b = src_dir / "b.bin"
    file_b.write_bytes(_random_bytes(500 * 1024, 0x5678))

    creator = PeerConnection(polite=False, ice_servers=[], on_send_signal=_noop_signal)
    joiner = PeerConnection(polite=True, ice_servers=[], on_send_signal=_noop_signal)
    await _connect(creator, joiner)

    entries = build_manifest([file_a, file_b])
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()
    storage = FilesystemStorage(dest_dir, "integration-1")

    ready_files: list[FileReadyResult] = []
    background_tasks: set[asyncio.Task[None]] = set()

    def _on_offer(_manifest: object) -> None:
        task = asyncio.ensure_future(receiver.accept())
        background_tasks.add(task)
        task.add_done_callback(background_tasks.discard)

    receiver = TransferReceiver(
        storage=storage,
        control=joiner.control_channel,
        on_offer=_on_offer,
        on_file_ready=ready_files.append,
    )
    joiner.on_control_message = lambda raw: receiver.handle_control_message(
        _peer_message_adapter.validate_json(raw)
    )
    joiner.on_data_frame = receiver.handle_data_frame

    sender = TransferSender(
        transfer_id="integration-1",
        entries=entries,
        control=creator.control_channel,
        data=creator.data_channel,
    )
    creator.on_control_message = lambda raw: sender.handle_control_message(
        _peer_message_adapter.validate_json(raw)
    )

    sender.start()

    async def _wait_done() -> None:
        while not (sender.get_phase() == "completed" and len(ready_files) == 2):
            await asyncio.sleep(0.05)

    await asyncio.wait_for(_wait_done(), timeout=15)

    assert sender.get_phase() == "completed"
    assert receiver.get_phase() == "completed"

    result_a = next(r for r in ready_files if r.offer.path == "a.bin")
    result_b = next(r for r in ready_files if r.offer.path == "b.bin")

    assert hash_block_hex(file_a.read_bytes()) == hash_block_hex(result_a.path.read_bytes())
    assert hash_block_hex(file_b.read_bytes()) == hash_block_hex(result_b.path.read_bytes())

    await creator.close()
    await joiner.close()
