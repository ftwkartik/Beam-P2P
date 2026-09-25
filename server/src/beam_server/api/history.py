"""Transfer-history endpoints (Milestone 11).

Every route is behind `get_current_user` (api/auth.py), and every service call takes
that user's own ID -- there is no endpoint here that can read or write another user's
rows (docs/security.md's isolation rule; see also db/repositories.py's
`TransferRepository`).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from beam_server.api.auth import get_current_user
from beam_server.db.engine import get_session
from beam_server.db.models import Transfer, User
from beam_server.db.repositories import TransferFileInput, TransferRepository
from beam_server.errors import NotFoundError
from beam_server.services.history import HistoryService

router = APIRouter(prefix="/transfers", tags=["history"])


def get_history_service(session: Annotated[AsyncSession, Depends(get_session)]) -> HistoryService:
    return HistoryService(transfers=TransferRepository(session))


class TransferFileRequest(BaseModel):
    name: str = Field(max_length=4096)
    size: int = Field(ge=0)
    sha256: str = Field(min_length=64, max_length=64)
    verified: bool = True


class RecordTransferRequest(BaseModel):
    direction: str = Field(pattern="^(sent|received)$")
    peer_label: str = Field(max_length=256)
    outcome: str = Field(pattern="^(completed|failed|cancelled|declined)$")
    files: list[TransferFileRequest] = Field(max_length=10_000)


class TransferFileResponse(BaseModel):
    name: str
    size: int
    sha256: str
    verified: bool


class TransferResponse(BaseModel):
    id: str
    direction: str
    peer_label: str
    outcome: str
    total_bytes: int
    files: list[TransferFileResponse]


def _to_response(transfer: Transfer) -> TransferResponse:
    return TransferResponse(
        id=str(transfer.id),
        direction=transfer.direction,
        peer_label=transfer.peer_label,
        outcome=transfer.outcome,
        total_bytes=transfer.total_bytes,
        files=[
            TransferFileResponse(name=f.name, size=f.size, sha256=f.sha256, verified=f.verified)
            for f in transfer.files
        ],
    )


@router.post("", status_code=201, response_model=TransferResponse)
async def record_transfer(
    body: RecordTransferRequest,
    user: Annotated[User, Depends(get_current_user)],
    history_service: Annotated[HistoryService, Depends(get_history_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TransferResponse:
    total_bytes = sum(f.size for f in body.files)
    transfer = await history_service.record(
        owner_id=user.id,
        direction=body.direction,
        peer_label=body.peer_label,
        outcome=body.outcome,
        total_bytes=total_bytes,
        files=[
            TransferFileInput(name=f.name, size=f.size, sha256=f.sha256, verified=f.verified)
            for f in body.files
        ],
    )
    await session.commit()
    return _to_response(transfer)


@router.get("", response_model=list[TransferResponse])
async def list_transfers(
    user: Annotated[User, Depends(get_current_user)],
    history_service: Annotated[HistoryService, Depends(get_history_service)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[TransferResponse]:
    transfers = await history_service.list_for_owner(user.id, limit=limit, offset=offset)
    return [_to_response(t) for t in transfers]


@router.get("/{transfer_id}", response_model=TransferResponse)
async def get_transfer(
    transfer_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    history_service: Annotated[HistoryService, Depends(get_history_service)],
) -> TransferResponse:
    transfer = await history_service.get_for_owner(user.id, transfer_id)
    if transfer is None:
        raise NotFoundError("No transfer with that ID.")
    return _to_response(transfer)
