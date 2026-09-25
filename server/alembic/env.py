"""Alembic migration environment, run async (the app only ever uses an async engine --
see db/engine.py) and reading `DATABASE_URL` from `Settings`, not a second copy of it
in alembic.ini, so the two can never drift apart.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

from beam_server.config import get_settings
from beam_server.db.engine import create_engine
from beam_server.db.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    database_url = get_settings().database_url
    if not database_url:
        raise RuntimeError("DATABASE_URL is not set; nothing to migrate against.")
    engine: AsyncEngine = create_engine(database_url)
    async with engine.connect() as connection:
        await connection.run_sync(_run_migrations)
    await engine.dispose()


def run_migrations_offline() -> None:
    context.configure(url=get_settings().database_url, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
