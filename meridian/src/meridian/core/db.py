"""The connection pool, and the one place a transaction is opened.

Raw SQL through asyncpg rather than an ORM. The queries this system runs are few
and shaped by the domain types they return, so an ORM would add a mapping layer
between two things that already agree.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg

from meridian.core.config import settings

# Supabase's transaction pooler on port 6543 does not support prepared
# statements, which asyncpg uses by default. Disabling the cache lets the same
# code run against the pooler, the session port and a local Postgres, at the
# cost of re-planning each query — irrelevant at this query volume.
_STATEMENT_CACHE_SIZE = 0

_pool: asyncpg.Pool | None = None


async def pool() -> asyncpg.Pool:
    """The shared pool, created on first use."""
    global _pool  # noqa: PLW0603 - one pool per process, created lazily
    if _pool is None:
        _pool = await asyncpg.create_pool(
            settings().requires_database(),
            min_size=1,
            max_size=10,
            statement_cache_size=_STATEMENT_CACHE_SIZE,
        )
    return _pool


async def close_pool() -> None:
    """Release every connection. Called on shutdown and between test runs."""
    global _pool  # noqa: PLW0603
    if _pool is not None:
        await _pool.close()
        _pool = None


@asynccontextmanager
async def transaction() -> AsyncIterator[asyncpg.Connection]:
    """A connection inside a transaction, rolled back if the body raises.

    Freezing a spec has to write the snapshot and its copied assertions together
    or not at all, so writes that span tables take one of these rather than
    several separate calls.
    """
    connection = await (await pool()).acquire()
    try:
        async with connection.transaction():
            yield connection
    finally:
        await (await pool()).release(connection)
