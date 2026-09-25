"""The async SQLAlchemy engine and session factory for the accounts/history DB.

One engine per process, built lazily from `Settings.database_url` (see main.py's
lifespan) -- a deployment that leaves `DATABASE_URL` unset never opens a connection
pool it isn't using, consistent with accounts being an optional feature, not a
foundation everything else depends on.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import cast

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from starlette.requests import HTTPConnection


def create_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(database_url, pool_pre_ping=True)


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session(connection: HTTPConnection) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding one request-scoped session from the app-wide
    session factory (see main.py's lifespan) -- the same `app.state` pattern as
    store/redis.py's `get_redis`."""
    factory = cast("async_sessionmaker[AsyncSession]", connection.app.state.db_session_factory)
    async with factory() as session:
        yield session
