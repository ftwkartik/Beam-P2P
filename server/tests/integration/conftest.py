"""Integration-test fixtures that need a real Redis or Postgres instance.

These tests are skipped (not failed) when the service is unreachable, so `pytest`
still runs cleanly on a machine that hasn't started `docker compose up -d redis
postgres`. CI always provides both as service containers (see
.github/workflows/ci.yml).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
import redis.asyncio as redis
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from beam_server.db.engine import create_engine, make_session_factory
from beam_server.db.models import Base

INTEGRATION_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")
#: Same server as the dev stack's `beam` database, a different logical database --
#: the same isolation trick `real_redis` uses db 15 for, so running the suite never
#: touches whatever a developer has sitting in their own dev database.
INTEGRATION_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://beam:beam@localhost:5432/beam_test",  # pragma: allowlist secret
)

_TABLE_NAMES = ("transfer_files", "transfers", "refresh_tokens", "users")


@pytest.fixture
async def real_redis() -> AsyncIterator[redis.Redis]:
    client = redis.from_url(INTEGRATION_REDIS_URL, decode_responses=True)
    try:
        await client.ping()
    except redis.RedisError:
        await client.aclose()
        pytest.skip(
            f"Redis not reachable at {INTEGRATION_REDIS_URL}; "
            "run `docker compose up -d redis` to enable integration tests."
        )
    else:
        await client.flushdb()
        yield client
        await client.flushdb()
        await client.aclose()


async def _make_postgres_engine() -> AsyncEngine | None:
    engine = create_engine(INTEGRATION_DATABASE_URL)
    try:
        async with engine.connect() as conn:
            await conn.run_sync(lambda c: None)
    except SQLAlchemyError:
        await engine.dispose()
        return None
    return engine


@pytest.fixture
async def real_postgres() -> AsyncIterator[AsyncSession]:
    """A real Postgres-backed session against a disposable `beam_test` database
    (`CREATE DATABASE beam_test;` once, by hand or via a CI step -- this fixture
    doesn't create the database itself, only its tables, matching how `real_redis`
    assumes the server already exists and only resets its own content)."""
    engine = await _make_postgres_engine()
    if engine is None:
        pytest.skip(
            f"Postgres not reachable at {INTEGRATION_DATABASE_URL}; run `docker "
            "compose up -d postgres` and `createdb -h localhost -U beam beam_test` "
            "to enable integration tests."
        )
        return
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = make_session_factory(engine)
    async with session_factory() as session:
        yield session
    async with engine.begin() as conn:
        for table in _TABLE_NAMES:
            await conn.exec_driver_sql(f'TRUNCATE TABLE "{table}" CASCADE')
    await engine.dispose()
