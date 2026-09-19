"""The receiving side of a file transfer (docs/protocol.md §4). Direct counterpart of
the web client's `engine/transfer/receiver.ts`, storage-agnostic in the same spirit
(`FilesystemStorage` here instead of OPFS); consent is a caller decision, surfaced via
`on_offer` and acted on by calling `accept()`/`decline()`. Control messages are the
same Pydantic models `beam_protocol.peer` defines for the wire format, not hand-rolled
dicts -- see sender.py's module docstring.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from beam_cli.channels import ControlChannelLike
from beam_cli.storage import FileStorageHandle, FilesystemStorage, PersistedFileState
from beam_protocol.bitmap import BlockBitmap, indices_to_ranges
from beam_protocol.frames import Frame
from beam_protocol.hashing import derive_file_root_hash, hash_block, hash_block_hex
from beam_protocol.peer import (
    AcceptMessage,
    AckMessage,
    BlockMessage,
    CancelMessage,
    DeclineMessage,
    FileDoneMessage,
    FileOffer,
    FileVerifiedMessage,
    NackMessage,
    OfferFilesMessage,
    PeerMessage,
)

#: docs/protocol.md §4.1: ack ranges are batched, every 8 blocks or 250 ms.
ACK_BATCH_SIZE = 8
ACK_BATCH_SECONDS = 0.25

ReceiverPhase = Literal[
    "waiting_for_offer", "offered", "declined", "receiving", "completed", "failed", "cancelled"
]


@dataclass(frozen=True, slots=True)
class OfferedManifest:
    transfer_id: str
    block_size: int
    files: list[FileOffer]


@dataclass(frozen=True, slots=True)
class ReceiverProgress:
    file_index: int
    file_count: int
    bytes_verified_for_file: int
    file_size: int
    total_bytes_verified: int
    total_bytes: int


@dataclass(frozen=True, slots=True)
class FileReadyResult:
    file_index: int
    offer: FileOffer
    path: Path


def _terminal(phase: ReceiverPhase) -> bool:
    return phase in ("completed", "failed", "declined", "cancelled")


@dataclass
class _FileState:
    offer: FileOffer
    handle: FileStorageHandle
    block_count: int
    bitmap: BlockBitmap
    block_hashes: list[bytes | None]
    announced_block_hashes: dict[int, str] = field(default_factory=dict)
    bytes_received_by_block: dict[int, int] = field(default_factory=dict)
    pending_ack_indices: list[int] = field(default_factory=list)
    ack_timer: asyncio.TimerHandle | None = None
    expected_root_hash: str | None = None
    verified: bool = False


def _sum_verified_bytes(state: _FileState, block_size: int) -> int:
    total = 0
    for i in range(state.block_count):
        if state.bitmap.has(i):
            total += min(block_size, state.offer.size - i * block_size)
    return total


@dataclass
class TransferReceiver:
    storage: FilesystemStorage
    control: ControlChannelLike
    on_offer: Callable[[OfferedManifest], None] | None = None
    on_phase_change: Callable[[ReceiverPhase], None] | None = None
    on_progress: Callable[[ReceiverProgress], None] | None = None
    on_file_ready: Callable[[FileReadyResult], None] | None = None
    on_error: Callable[[str], None] | None = None

    _phase: ReceiverPhase = field(default="waiting_for_offer", init=False)
    _manifest: OfferedManifest | None = field(default=None, init=False)
    _files_by_index: dict[int, _FileState] = field(default_factory=dict, init=False)
    _file_state_tasks: dict[int, asyncio.Task[_FileState]] = field(default_factory=dict, init=False)
    _resume_seeds: dict[int, PersistedFileState] = field(default_factory=dict, init=False)
    _total_bytes: int = field(default=0, init=False)
    _total_bytes_verified: int = field(default=0, init=False)
    #: Holds references to fire-and-forget tasks so asyncio doesn't garbage-collect
    #: them mid-flight (a bare `asyncio.ensure_future(...)` with nothing else
    #: referencing the task is exactly that hazard).
    _background_tasks: set[asyncio.Task[None]] = field(default_factory=set, init=False)

    def _spawn(self, coro: Coroutine[Any, Any, None]) -> None:
        task = asyncio.ensure_future(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    def _send(self, message: PeerMessage) -> None:
        self.control.send(message.model_dump_json(by_alias=True))

    def get_phase(self) -> ReceiverPhase:
        return self._phase

    def handle_control_message(self, message: PeerMessage) -> None:
        if isinstance(message, OfferFilesMessage):
            self._spawn(self._handle_offer(message))
        elif isinstance(message, BlockMessage):
            self._handle_block_announcement(message)
        elif isinstance(message, FileDoneMessage):
            self._spawn(self._handle_file_done(message))
        elif isinstance(message, CancelMessage):
            self._set_phase("cancelled")

    def handle_data_frame(self, raw: bytes) -> None:
        self._spawn(self._process_frame(raw))

    async def accept(self) -> None:
        """Accepts the pending offer (call after `on_offer` and user consent, or
        automatically when resuming a transfer already accepted before a reconnect)."""
        if self._manifest is None:
            return
        self._set_phase("receiving")

        self.storage.mark_accepted()
        have: dict[str, str] = {}
        for offer in self._manifest.files:
            saved = self.storage.load_state(offer.index)
            if saved is not None and saved.size == offer.size:
                self._resume_seeds[offer.index] = saved
                have[str(offer.index)] = saved.bitmap_base64

        self._send(AcceptMessage(transfer_id=self._manifest.transfer_id, have=have))

    def decline(self, reason: str) -> None:
        if self._manifest is None:
            return
        self._set_phase("declined")
        self._send(DeclineMessage(transfer_id=self._manifest.transfer_id, reason=reason))

    async def _handle_offer(self, message: OfferFilesMessage) -> None:
        self._manifest = OfferedManifest(
            transfer_id=message.transfer_id, block_size=message.block_size, files=message.files
        )
        self._total_bytes = sum(f.size for f in message.files)
        self._set_phase("offered")

        # The sender re-sends `offer_files` with the same transfer id after a
        # reconnect (a network drop while both processes keep running). If this
        # transfer was already accepted before, resume silently instead of asking
        # the user to consent to the same transfer twice.
        if self.storage.was_accepted():
            await self.accept()
            return
        if self.on_offer is not None:
            self.on_offer(self._manifest)

    def _ensure_file_state(self, file_index: int) -> asyncio.Task[_FileState]:
        """Memoizes the in-flight *task*, not just the resolved state: a block
        announcement and a data frame for the same new file can both call this
        before either's `storage.open_file()` resolves, and without this they'd
        race to create two separate handles for the same file."""
        task = self._file_state_tasks.get(file_index)
        if task is None:
            task = asyncio.ensure_future(self._create_file_state(file_index))
            self._file_state_tasks[file_index] = task
        return task

    async def _create_file_state(self, file_index: int) -> _FileState:
        assert self._manifest is not None
        offer = next(f for f in self._manifest.files if f.index == file_index)
        block_count = 0 if offer.size == 0 else -(-offer.size // self._manifest.block_size)

        handle = await self.storage.open_file(file_index, offer.size)
        state = _FileState(
            offer=offer,
            handle=handle,
            block_count=block_count,
            bitmap=BlockBitmap(block_count),
            block_hashes=[None] * block_count,
        )

        seed = self._resume_seeds.get(file_index)
        if seed is not None:
            state.bitmap = BlockBitmap.from_base64(seed.bitmap_base64, block_count)
            for i in range(block_count):
                hex_hash = seed.block_hashes_hex[i] if i < len(seed.block_hashes_hex) else None
                if hex_hash:
                    state.block_hashes[i] = bytes.fromhex(hex_hash)
            resumed_bytes = _sum_verified_bytes(state, self._manifest.block_size)
            self._total_bytes_verified += resumed_bytes
            self._report_progress(state)

        self._files_by_index[file_index] = state
        return state

    def _handle_block_announcement(self, message: BlockMessage) -> None:
        file_index = message.file
        block_index = message.index
        sha256 = message.sha256

        async def _apply() -> None:
            state = await self._ensure_file_state(file_index)
            state.announced_block_hashes[block_index] = sha256

        self._spawn(_apply())

    def _block_range(self, state: _FileState, block_index: int) -> tuple[int, int]:
        assert self._manifest is not None
        start = block_index * self._manifest.block_size
        end = min(start + self._manifest.block_size, state.offer.size)
        return start, end

    async def _process_frame(self, raw: bytes) -> None:
        try:
            frame = Frame.unpack(raw)
            state = await self._ensure_file_state(frame.file_index)
            assert self._manifest is not None

            await state.handle.write_at(frame.offset, frame.payload)

            block_index = frame.offset // self._manifest.block_size
            received = state.bytes_received_by_block.get(block_index, 0) + len(frame.payload)
            state.bytes_received_by_block[block_index] = received

            start, end = self._block_range(state, block_index)
            if received >= end - start:
                await self._verify_block(state, block_index, start, end)
        except Exception as exc:
            if self.on_error is not None:
                self.on_error(str(exc))

    async def _verify_block(
        self, state: _FileState, block_index: int, start: int, end: int
    ) -> None:
        announced = state.announced_block_hashes.get(block_index)
        if announced is None:
            return  # the block message hasn't arrived yet; re-checked once it does

        block_bytes = await state.handle.read_range(start, end)
        actual_hex = hash_block_hex(block_bytes)

        if actual_hex != announced:
            state.bytes_received_by_block[block_index] = 0
            self._send(NackMessage(file=state.offer.index, index=block_index))
            return

        state.block_hashes[block_index] = hash_block(block_bytes)
        state.bitmap.set(block_index)
        self._total_bytes_verified += end - start
        self._queue_ack(state, block_index)
        self._report_progress(state)

        if state.bitmap.is_complete() and state.expected_root_hash is not None:
            await self._finalize_file(state)

    def _queue_ack(self, state: _FileState, block_index: int) -> None:
        state.pending_ack_indices.append(block_index)
        if len(state.pending_ack_indices) >= ACK_BATCH_SIZE:
            self._flush_ack(state)
            return
        if state.ack_timer is None:
            loop = asyncio.get_event_loop()
            state.ack_timer = loop.call_later(ACK_BATCH_SECONDS, lambda: self._flush_ack(state))

    def _flush_ack(self, state: _FileState) -> None:
        if state.ack_timer is not None:
            state.ack_timer.cancel()
            state.ack_timer = None
        if not state.pending_ack_indices:
            return
        ranges = indices_to_ranges(state.pending_ack_indices)
        state.pending_ack_indices = []
        self._send(AckMessage(file=state.offer.index, verified=ranges))
        self._persist_file_state(state)

    def _persist_file_state(self, state: _FileState) -> None:
        """Batched at the same cadence as acks (docs/protocol.md §4.1), not per block."""
        self.storage.save_state(
            state.offer.index,
            PersistedFileState(
                size=state.offer.size,
                bitmap_base64=state.bitmap.to_base64(),
                block_hashes_hex=[h.hex() if h else None for h in state.block_hashes],
            ),
        )

    def _report_progress(self, state: _FileState) -> None:
        assert self._manifest is not None
        bytes_verified_for_file = (
            state.offer.size
            if state.bitmap.count() == state.block_count
            else _sum_verified_bytes(state, self._manifest.block_size)
        )
        if self.on_progress is not None:
            self.on_progress(
                ReceiverProgress(
                    file_index=state.offer.index,
                    file_count=len(self._manifest.files),
                    bytes_verified_for_file=bytes_verified_for_file,
                    file_size=state.offer.size,
                    total_bytes_verified=self._total_bytes_verified,
                    total_bytes=self._total_bytes,
                )
            )

    async def _handle_file_done(self, message: FileDoneMessage) -> None:
        state = await self._ensure_file_state(message.file)
        state.expected_root_hash = message.sha256
        if state.bitmap.is_complete():
            await self._finalize_file(state)

    async def _finalize_file(self, state: _FileState) -> None:
        if state.verified or state.expected_root_hash is None:
            return
        state.verified = True
        self._flush_ack(state)
        self._persist_file_state(state)

        block_hashes = [h if h is not None else b"" for h in state.block_hashes]
        actual_root_hash = derive_file_root_hash(state.offer.size, block_hashes)
        ok = actual_root_hash == state.expected_root_hash
        self._send(FileVerifiedMessage(file=state.offer.index, ok=ok))

        if not ok:
            self._set_phase("failed")
            if self.on_error is not None:
                self.on_error(f"File {state.offer.index} failed root-hash verification")
            return

        destination = self.storage.destination_path(state.offer.path)
        final_path = await state.handle.finalize(destination)
        if self.on_file_ready is not None:
            result = FileReadyResult(
                file_index=state.offer.index, offer=state.offer, path=final_path
            )
            self.on_file_ready(result)

        assert self._manifest is not None
        all_files_touched = len(self._files_by_index) == len(self._manifest.files)
        if all_files_touched and all(f.verified for f in self._files_by_index.values()):
            self._set_phase("completed")

    def _set_phase(self, phase: ReceiverPhase) -> None:
        if phase == self._phase:
            return
        self._phase = phase
        if self.on_phase_change is not None:
            self.on_phase_change(phase)
        if self._manifest is not None and _terminal(phase):
            self.storage.clear_transfer()
