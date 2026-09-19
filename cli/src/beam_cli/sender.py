"""The sending side of a file transfer (docs/protocol.md §4). Direct counterpart of
the web client's `engine/transfer/sender.ts`, adapted for asyncio and real files on
disk instead of a browser `File`. Control messages are built as the same Pydantic
models `beam_protocol.peer` already defines for the wire format (the server and the
web client's generated TS types come from the same schema), not hand-rolled dicts.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any, Literal

from aiortc import RTCDataChannel

from beam_cli.channels import ControlChannelLike
from beam_cli.manifest import ManifestEntry
from beam_protocol.bitmap import BlockBitmap
from beam_protocol.constants import DEFAULT_BLOCK_SIZE, DEFAULT_FRAME_PAYLOAD_SIZE
from beam_protocol.frames import Frame
from beam_protocol.hashing import derive_file_root_hash, hash_block, hash_block_hex
from beam_protocol.peer import (
    AcceptMessage,
    BlockMessage,
    CancelMessage,
    DeclineMessage,
    FileDoneMessage,
    FileOffer,
    FileVerifiedMessage,
    NackMessage,
    OfferFilesMessage,
    PeerMessage,
    TransferDoneMessage,
)

#: docs/protocol.md §4.2: the sender pauses above this and resumes at the data
#: channel's bufferedamountlow threshold (set by peer.py when creating the channel).
HIGH_WATERMARK_BYTES = 4 * 1024 * 1024
MAX_BLOCK_RETRIES = 3

SenderPhase = Literal["offering", "declined", "sending", "completed", "failed", "cancelled"]


@dataclass(frozen=True, slots=True)
class SenderProgress:
    file_index: int
    file_count: int
    bytes_sent_for_file: int
    file_size: int
    total_bytes_sent: int
    total_bytes: int


def _block_count_for(size: int, block_size: int) -> int:
    return 0 if size == 0 else -(-size // block_size)  # ceil division


@dataclass
class TransferSender:
    transfer_id: str
    entries: list[ManifestEntry]
    control: ControlChannelLike
    data: RTCDataChannel
    block_size: int = DEFAULT_BLOCK_SIZE
    frame_size: int = DEFAULT_FRAME_PAYLOAD_SIZE
    on_phase_change: Callable[[SenderPhase], None] | None = None
    on_progress: Callable[[SenderProgress], None] | None = None
    #: A file failed verification, was declined, or a block failed 3 retries.
    on_error: Callable[[str], None] | None = None

    _phase: SenderPhase = field(default="offering", init=False)
    _total_bytes: int = field(default=0, init=False)
    _total_bytes_sent: int = field(default=0, init=False)
    _have_by_file: dict[int, BlockBitmap] = field(default_factory=dict, init=False)
    _retries_by_block: dict[tuple[int, int], int] = field(default_factory=dict, init=False)
    _pending_resends: list[NackMessage] = field(default_factory=list, init=False)
    _resend_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    #: Holds references to fire-and-forget tasks so asyncio doesn't garbage-collect
    #: them mid-flight (a bare `asyncio.ensure_future(...)` with nothing else
    #: referencing the task is exactly that hazard).
    _background_tasks: set[asyncio.Task[None]] = field(default_factory=set, init=False)

    def __post_init__(self) -> None:
        self._total_bytes = sum(e.offer.size for e in self.entries)

    def _spawn(self, coro: Coroutine[Any, Any, None]) -> None:
        task = asyncio.ensure_future(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    def _send(self, message: PeerMessage) -> None:
        self.control.send(message.model_dump_json(by_alias=True))

    def get_phase(self) -> SenderPhase:
        return self._phase

    def start(self) -> None:
        """Sends the `offer_files` manifest. Call once; wait for the receiver's
        response via `handle_control_message`."""
        self._set_phase("offering")
        self._send(
            OfferFilesMessage(
                transfer_id=self.transfer_id,
                block_size=self.block_size,
                files=[e.offer for e in self.entries],
            )
        )

    def handle_control_message(self, message: PeerMessage) -> None:
        if isinstance(message, AcceptMessage):
            self._handle_accept(message)
        elif isinstance(message, DeclineMessage):
            self._set_phase("declined")
            self._error(f"Declined: {message.reason}")
        elif isinstance(message, NackMessage):
            self._pending_resends.append(message)
            self._spawn(self._drain_resends())
        elif isinstance(message, FileVerifiedMessage):
            if not message.ok:
                self._set_phase("failed")
                self._error(f"File {message.file} failed verification")
        elif isinstance(message, CancelMessage):
            self._set_phase("cancelled")

    def _handle_accept(self, message: AcceptMessage) -> None:
        for key, base64_bitmap in message.have.items():
            file_index = int(key)
            entry = next((e for e in self.entries if e.offer.index == file_index), None)
            if entry is None:
                continue
            block_count = _block_count_for(entry.offer.size, self.block_size)
            bitmap = BlockBitmap.from_base64(base64_bitmap, block_count)
            self._have_by_file[file_index] = bitmap
        self._spawn(self._send_all_files())

    async def _send_all_files(self) -> None:
        self._set_phase("sending")
        try:
            for entry in self.entries:
                if self._phase != "sending":
                    return
                await self._send_file(entry)
            if self._phase == "sending":
                self._send(TransferDoneMessage(transfer_id=self.transfer_id))
                self._set_phase("completed")
        except Exception as exc:
            self._set_phase("failed")
            self._error(str(exc))

    async def _send_file(self, entry: ManifestEntry) -> None:
        offer = entry.offer
        block_count = _block_count_for(offer.size, self.block_size)
        have = self._have_by_file.get(offer.index)
        block_hashes: list[bytes] = [b""] * block_count
        bytes_sent_for_file = 0

        with entry.source_path.open("rb") as f:
            for block_index in range(block_count):
                if self._phase != "sending":
                    return
                start = block_index * self.block_size
                end = min(start + self.block_size, offer.size)

                f.seek(start)
                block_bytes = f.read(end - start)

                if have is not None and have.has(block_index):
                    # Still read and hash a skipped block: a file changed since a
                    # previous session must be detected via a root-hash mismatch
                    # (docs/protocol.md §4.3), not silently trusted.
                    block_hashes[block_index] = hash_block(block_bytes)
                    continue

                block_hash = await self._send_block(offer.index, block_index, block_bytes)
                block_hashes[block_index] = block_hash
                bytes_sent_for_file += end - start
                self._total_bytes_sent += end - start
                self._report_progress(offer, bytes_sent_for_file)

        root_hash = derive_file_root_hash(offer.size, block_hashes)
        self._send(FileDoneMessage(file=offer.index, sha256=root_hash))

    async def _send_block(self, file_index: int, block_index: int, block_bytes: bytes) -> bytes:
        block_hash = hash_block(block_bytes)
        block_hash_hex = hash_block_hex(block_bytes)

        self._send(BlockMessage(file=file_index, index=block_index, sha256=block_hash_hex))
        await self._send_frames(file_index, block_index * self.block_size, block_bytes)
        return block_hash

    async def _send_frames(self, file_index: int, block_start: int, block_bytes: bytes) -> None:
        for offset in range(0, len(block_bytes), self.frame_size):
            await self._wait_for_buffered_amount_below_high_watermark()
            chunk = block_bytes[offset : offset + self.frame_size]
            is_last = offset + len(chunk) >= len(block_bytes)
            frame = Frame(
                file_index=file_index,
                offset=block_start + offset,
                payload=chunk,
                last_of_block=is_last,
            )
            self.data.send(frame.pack())

    async def _wait_for_buffered_amount_below_high_watermark(self) -> None:
        if self.data.bufferedAmount <= HIGH_WATERMARK_BYTES:
            return
        low = asyncio.Event()
        self.data.once("bufferedamountlow", low.set)
        await low.wait()

    async def _drain_resends(self) -> None:
        async with self._resend_lock:
            while self._pending_resends:
                message = self._pending_resends.pop(0)
                await self._resend_block(message)

    async def _resend_block(self, message: NackMessage) -> None:
        key = (message.file, message.index)
        retries = self._retries_by_block.get(key, 0)
        if retries >= MAX_BLOCK_RETRIES:
            self._set_phase("failed")
            self._error(
                f"Block {message.index} of file {message.file} "
                f"failed after {MAX_BLOCK_RETRIES} retries"
            )
            return
        self._retries_by_block[key] = retries + 1

        entry = next((e for e in self.entries if e.offer.index == message.file), None)
        if entry is None:
            return

        start = message.index * self.block_size
        end = min(start + self.block_size, entry.offer.size)
        with entry.source_path.open("rb") as f:
            f.seek(start)
            block_bytes = f.read(end - start)
        await self._send_block(message.file, message.index, block_bytes)

    def _report_progress(self, offer: FileOffer, bytes_sent_for_file: int) -> None:
        if self.on_progress is not None:
            self.on_progress(
                SenderProgress(
                    file_index=offer.index,
                    file_count=len(self.entries),
                    bytes_sent_for_file=bytes_sent_for_file,
                    file_size=offer.size,
                    total_bytes_sent=self._total_bytes_sent,
                    total_bytes=self._total_bytes,
                )
            )

    def _set_phase(self, phase: SenderPhase) -> None:
        if phase == self._phase:
            return
        self._phase = phase
        if self.on_phase_change is not None:
            self.on_phase_change(phase)

    def _error(self, message: str) -> None:
        if self.on_error is not None:
            self.on_error(message)
