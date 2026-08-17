"""Shared pytest fixtures.

``tests/`` mirrors the package. Most of it touches no database; anything that
does asks for the ``connection`` fixture below, is marked ``db``, and runs
against the local container from ``make db`` — never against Supabase.

The connection fixture lives here rather than beside the repository tests
because the reviewer needs it too: a round is a transaction over a board, so
proving the loop means proving it against a real one.
"""

from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import pytest

from meridian.core.config import settings
from meridian.core.db import close_pool, pool
from meridian.domain.graph import Board
from meridian.seed import COMPLETE, SEED, board_from_file

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Absolute path to the repository root."""
    return REPO_ROOT


@pytest.fixture
def seed() -> Board:
    """The pre-alert board as a process owner would first draw it.

    Deliberately incomplete. The gaps are listed in the seed file's own header,
    and several tests hold it to exactly that list.
    """
    return board_from_file(SEED)


@pytest.fixture
def complete() -> Board:
    """The same board once a review has finished with it.

    Beside ``seed`` because the pair is what makes the reviewer measurable, and
    because several structures only exist on this one: the way back after a
    discrepancy is reported, and two outcomes routed to a single step.
    """
    return board_from_file(COMPLETE)


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
