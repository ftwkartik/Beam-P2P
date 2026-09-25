"""accounts/history baseline: users, refresh_tokens, transfers, transfer_files

Revision ID: 0001
Revises:
Create Date: 2026-09-25 09:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String, nullable=False, unique=True),
        sa.Column("password_hash", sa.String, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "refresh_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String, nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])

    op.create_table(
        "transfers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "owner_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("direction", sa.String, nullable=False),
        sa.Column("peer_label", sa.String, nullable=False),
        sa.Column("outcome", sa.String, nullable=False),
        sa.Column("total_bytes", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("direction in ('sent', 'received')", name="ck_transfers_direction"),
        sa.CheckConstraint(
            "outcome in ('completed', 'failed', 'cancelled', 'declined')",
            name="ck_transfers_outcome",
        ),
    )
    op.create_index("ix_transfers_owner_id_created_at", "transfers", ["owner_id", "created_at"])

    op.create_table(
        "transfer_files",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "transfer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("transfers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("size", sa.BigInteger, nullable=False),
        sa.Column("sha256", sa.String, nullable=False),
        sa.Column("verified", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("transfer_id", "name", name="uq_transfer_files_transfer_id_name"),
    )


def downgrade() -> None:
    op.drop_table("transfer_files")
    op.drop_index("ix_transfers_owner_id_created_at", table_name="transfers")
    op.drop_table("transfers")
    op.drop_index("ix_refresh_tokens_user_id", table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
    op.drop_table("users")
