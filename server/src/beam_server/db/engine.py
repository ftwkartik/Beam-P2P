"""The async SQLAlchemy engine and session factory for the accounts/history DB.

One engine per process, built lazily from `Settings.database_url` (see main.py's
lifespan) -- a deployment that leaves `DATABASE_URL` unset never opens a connection
pool it isn't using, consistent with accounts being an optional feature, not a
foundation everything else depends on.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(database_url, pool_pre_ping=True)


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session_from(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """A FastAPI dependency body, parameterized by the app's own session factory (see
    api/auth.py's `get_db_session`) rather than a module-level global -- tests build
    their own engine/factory pointed at a disposable database (see
    server/tests/integration/conftest.py's `real_postgres`)."""
    async with session_factory() as session:
        yield session
