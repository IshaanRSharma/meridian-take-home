"""Shared pytest fixtures.

``tests/`` mirrors the package and touches no database (Claude.md §3). Anything
that needs Postgres is marked ``db`` and runs against the local container from
``make db`` — never against Supabase.
"""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Absolute path to the repository root."""
    return REPO_ROOT
