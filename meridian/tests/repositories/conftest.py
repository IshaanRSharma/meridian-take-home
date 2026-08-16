"""A connection to the local test database, whose work is never committed.

Two separate protections, because they cover different failures.

Every test runs inside a transaction that is rolled back, so one test never
sees another's writes. And the database is the container ``make db`` starts —
never Supabase — because a rollback cannot undo a migration, and applying
migrations is exactly what preparing a test database involves.
``Settings.requires_test_database`` refuses to hand over the production URL.

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
    """A connection to the test database, always rolled back."""
    try:
        acquired = await pool(settings().requires_test_database())
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"no test database — run `make db` ({exc})")

    async with acquired.acquire() as conn:
        transaction = conn.transaction()
        await transaction.start()
        try:
            yield conn
        finally:
            await transaction.rollback()
