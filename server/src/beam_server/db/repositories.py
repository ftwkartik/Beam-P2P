"""Postgres-backed repositories for accounts/history (Milestone 11).

Every transfer-history method takes `owner_id` and filters on it -- there is no
unscoped read here, the same isolation rule docs/security.md's isolation section
states for user-facing data: another user's rows are simply not reachable through
this repository, not merely hidden by a check the caller could forget to make.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from beam_server.db.models import RefreshToken, Transfer, TransferFile, User


@dataclass(frozen=True, slots=True)
class TransferFileInput:
    name: str
    size: int
    sha256: str
    verified: bool


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_email(self, email: str) -> User | None:
        result = await self._session.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        return await self._session.get(User, user_id)

    def create(self, *, email: str, password_hash: str) -> User:
        user = User(email=email, password_hash=password_hash)
        self._session.add(user)
        return user


class RefreshTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def create(self, *, user_id: uuid.UUID, token_hash: str, expires_at: datetime) -> RefreshToken:
        token = RefreshToken(user_id=user_id, token_hash=token_hash, expires_at=expires_at)
        self._session.add(token)
        return token

    async def get_by_hash(self, token_hash: str) -> RefreshToken | None:
        result = await self._session.execute(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )
        return result.scalar_one_or_none()

    async def revoke(self, token: RefreshToken, *, revoked_at: datetime) -> None:
        token.revoked_at = revoked_at


class TransferRepository:
    """Owner-scoped: every method takes `owner_id` and every query filters on it."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def create(
        self,
        *,
        owner_id: uuid.UUID,
        direction: str,
        peer_label: str,
        outcome: str,
        total_bytes: int,
        files: list[TransferFileInput],
    ) -> Transfer:
        transfer = Transfer(
            owner_id=owner_id,
            direction=direction,
            peer_label=peer_label,
            outcome=outcome,
            total_bytes=total_bytes,
            files=[
                TransferFile(name=f.name, size=f.size, sha256=f.sha256, verified=f.verified)
                for f in files
            ],
        )
        self._session.add(transfer)
        return transfer

    async def list_for_owner(
        self, owner_id: uuid.UUID, *, limit: int, offset: int
    ) -> list[Transfer]:
        result = await self._session.execute(
            select(Transfer)
            .where(Transfer.owner_id == owner_id)
            .order_by(Transfer.created_at.desc())
            .limit(limit)
            .offset(offset)
            # Without this, `.files` lazy-loads on first access -- async lazy loading
            # needs an active greenlet bridge that plain attribute access outside an
            # awaited call doesn't have, raising MissingGreenlet. Found by actually
            # calling this endpoint, not by inspection.
            .options(selectinload(Transfer.files))
        )
        return list(result.scalars().unique())

    async def get_for_owner(self, owner_id: uuid.UUID, transfer_id: uuid.UUID) -> Transfer | None:
        """Returns `None` both when the transfer doesn't exist and when it belongs to
        someone else -- the two cases are indistinguishable to the caller on purpose
        (docs/security.md: a cross-user lookup returns 404, not a leak of existence)."""
        result = await self._session.execute(
            select(Transfer)
            .where(Transfer.id == transfer_id, Transfer.owner_id == owner_id)
            .options(selectinload(Transfer.files))
        )
        return result.scalar_one_or_none()
