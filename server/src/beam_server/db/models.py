"""SQLAlchemy models for the accounts/history schema (docs/data-model.md §3).

Scoped to what Milestone 11 actually needs: users, their refresh tokens, and the
transfer-history records a client opts to write after a completed transfer. The
`devices` table docs/data-model.md sketches is deferred -- nothing here depends on it,
and it adds a whole device-registration flow for a feature (naming/listing a user's
devices) that isn't part of this milestone's exit criterion.

IDs are plain `uuid4`, not the `uuid7` docs/data-model.md mentions: Python 3.12's
stdlib has no UUIDv7 generator (that lands in 3.14), and pulling in a dependency just
for time-sortable primary keys isn't worth it when nothing here actually needs that
ordering -- `created_at` columns already give query-time ordering.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


#: `Mapped[datetime]` alone maps to a naive `DateTime` column -- every migrated column
#: here is `timestamptz` (timezone-aware), and every value this app ever writes is
#: `datetime.now(UTC)` (also aware). Without this, asyncpg's encoder raises on the
#: mismatch between an aware Python value and what SQLAlchemy thinks is a naive
#: column, found by actually running the login flow against a real Postgres, not by
#: inspection: `TypeError: can't subtract offset-naive and offset-aware datetimes`.
TZDateTime = Annotated[datetime, mapped_column(DateTime(timezone=True))]


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(unique=True)
    password_hash: Mapped[str]
    created_at: Mapped[TZDateTime] = mapped_column(server_default=func.now())

    refresh_tokens: Mapped[list[RefreshToken]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    transfers: Mapped[list[Transfer]] = relationship(
        back_populates="owner", cascade="all, delete-orphan"
    )


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    #: Never the raw token -- only its hash is stored (docs/security.md's pattern for
    #: anything bearer-token-shaped: a DB leak alone shouldn't hand out live sessions).
    token_hash: Mapped[str] = mapped_column(unique=True)
    expires_at: Mapped[TZDateTime]
    #: Set the moment this token is used to refresh (rotation) or on logout. A token
    #: presented again after that is reuse -- of a stolen or replayed token, since the
    #: legitimate client would have the *new* one instead -- and is rejected.
    revoked_at: Mapped[TZDateTime | None] = mapped_column(default=None)
    created_at: Mapped[TZDateTime] = mapped_column(server_default=func.now())

    user: Mapped[User] = relationship(back_populates="refresh_tokens")

    __table_args__ = (Index("ix_refresh_tokens_user_id", "user_id"),)


class Transfer(Base):
    """One transfer's metadata, written by the *client* after it completes (never by
    the server, which never sees file contents) -- docs/data-model.md §3."""

    __tablename__ = "transfers"

    id: Mapped[uuid.UUID] = _uuid_pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    direction: Mapped[str]
    peer_label: Mapped[str]
    outcome: Mapped[str]
    total_bytes: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[TZDateTime] = mapped_column(server_default=func.now())

    owner: Mapped[User] = relationship(back_populates="transfers")
    files: Mapped[list[TransferFile]] = relationship(
        back_populates="transfer", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("direction in ('sent', 'received')", name="ck_transfers_direction"),
        CheckConstraint(
            "outcome in ('completed', 'failed', 'cancelled', 'declined')",
            name="ck_transfers_outcome",
        ),
        Index("ix_transfers_owner_id_created_at", "owner_id", "created_at"),
    )


class TransferFile(Base):
    __tablename__ = "transfer_files"

    id: Mapped[uuid.UUID] = _uuid_pk()
    transfer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("transfers.id", ondelete="CASCADE"))
    name: Mapped[str]
    size: Mapped[int]
    sha256: Mapped[str]
    verified: Mapped[bool] = mapped_column(default=False)

    transfer: Mapped[Transfer] = relationship(back_populates="files")

    __table_args__ = (
        UniqueConstraint("transfer_id", "name", name="uq_transfer_files_transfer_id_name"),
    )
