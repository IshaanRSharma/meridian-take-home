"""A database connection that never commits.

Every repository test runs inside a transaction that is rolled back afterwards,
so the suite can run against the real Supabase project without leaving rows
behind or letting one test see another's writes.

The pool is closed after each test as well. It is a module-level global bound to
the event loop that created it, and pytest-asyncio gives each test a fresh loop
— so a pool that outlives its loop fails with "Event loop is closed" on the
second test rather than the first.
"""

from collections.abc import AsyncIterator

import asyncpg
import pytest

from meridian.core.config import settings
from meridian.core.db import close_pool, pool


@pytest.fixture(autouse=True)
async def _release_pool() -> AsyncIterator[None]:
    yield
    await close_pool()


@pytest.fixture
async def connection() -> AsyncIterator[asyncpg.Connection]:
    """A connection whose work is always rolled back."""
    if not settings().database_url:
        pytest.skip("DATABASE_URL is not set")

    async with (await pool()).acquire() as conn:
        transaction = conn.transaction()
        await transaction.start()
        try:
            yield conn
        finally:
            await transaction.rollback()
