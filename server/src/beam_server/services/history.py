"""Transfer-history recording and retrieval (Milestone 11).

The server never sees file contents (docs/data-model.md §3: "History is metadata
only, and opt-in"); this only ever stores what the client tells it after a transfer
has already finished on its own.
"""

from __future__ import annotations

import uuid

from beam_server.db.models import Transfer
from beam_server.db.repositories import TransferFileInput, TransferRepository

DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 200


class HistoryService:
    def __init__(self, *, transfers: TransferRepository) -> None:
        self._transfers = transfers

    async def record(
        self,
        *,
        owner_id: uuid.UUID,
        direction: str,
        peer_label: str,
        outcome: str,
        total_bytes: int,
        files: list[TransferFileInput],
    ) -> Transfer:
        return self._transfers.create(
            owner_id=owner_id,
            direction=direction,
            peer_label=peer_label,
            outcome=outcome,
            total_bytes=total_bytes,
            files=files,
        )

    async def list_for_owner(
        self, owner_id: uuid.UUID, *, limit: int = DEFAULT_LIST_LIMIT, offset: int = 0
    ) -> list[Transfer]:
        return await self._transfers.list_for_owner(
            owner_id, limit=min(limit, MAX_LIST_LIMIT), offset=offset
        )

    async def get_for_owner(self, owner_id: uuid.UUID, transfer_id: uuid.UUID) -> Transfer | None:
        return await self._transfers.get_for_owner(owner_id, transfer_id)
