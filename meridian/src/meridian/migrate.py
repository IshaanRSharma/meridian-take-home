"""Apply the SQL migrations in order.

Plain ``.sql`` files rather than a migration framework, because the target is a
managed Postgres and the file is the thing a reviewer should be able to read.
Applied filenames are recorded so re-running is safe.
"""

import asyncio
from pathlib import Path

from meridian.core.db import close_pool, transaction

MIGRATIONS = Path(__file__).resolve().parents[2] / "db" / "migrations"

_LEDGER = """
create table if not exists schema_migrations (
  filename   text primary key,
  applied_at timestamptz not null default now()
);
alter table schema_migrations enable row level security;
"""


def read_all(directory: Path = MIGRATIONS) -> list[tuple[str, str]]:
    """Every migration as (filename, sql), in order.

    Read before any async work starts, because file reads block and a migration
    runner has no reason to interleave them with database calls.
    """
    return [(path.name, path.read_text()) for path in sorted(directory.glob("*.sql"))]


async def apply(directory: Path = MIGRATIONS) -> list[str]:
    """Run every migration not yet recorded, and return the ones applied."""
    files = read_all(directory)
    applied: list[str] = []
    async with transaction() as connection:
        await connection.execute(_LEDGER)
        done = {
            r["filename"] for r in await connection.fetch("select filename from schema_migrations")
        }
        for name, sql in files:
            if name in done:
                continue
            await connection.execute(sql)
            await connection.execute("insert into schema_migrations (filename) values ($1)", name)
            applied.append(name)
    return applied


async def _main() -> None:
    applied = await apply()
    for name in applied:
        print(f"applied {name}")  # noqa: T201 - this is a command
    if not applied:
        print("nothing to apply")  # noqa: T201
    await close_pool()


if __name__ == "__main__":
    asyncio.run(_main())
