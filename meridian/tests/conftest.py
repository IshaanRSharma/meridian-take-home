"""Shared pytest fixtures.

``tests/`` mirrors the package and touches no database (Claude.md §3). Anything
that needs Postgres is marked ``db`` and runs against the local container from
``make db`` — never against Supabase.
"""

from pathlib import Path

import pytest

from meridian.domain.graph import Board
from meridian.seed import SEED, board_from_file

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
