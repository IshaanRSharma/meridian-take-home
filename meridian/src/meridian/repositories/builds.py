"""Builds: one row per attempt at implementing a spec.

Append-only in practice though not enforced by a trigger, because the row is a
claim about a commit and a commit does not change. `iteration` is allocated here
rather than supplied, so two registrations of the same spec can never collide on
a number that the reliability curve plots against.

`file_map` and `entry_point` both arrive from `build.json`, which whoever
generated the agent wrote. They are stored rather than re-read from disk at use
time for the same reason the spec is a blob: a build is a claim about code at a
sha, and reading the current file would answer a different question.
"""

from __future__ import annotations

import json
from uuid import UUID

import asyncpg

from meridian.domain.build import Build
from meridian.domain.errors import NotFoundError

_COLUMNS = (
    "id, spec_id, iteration, parent_build_id, source_ref, created_by, model, "
    "prompt_version, temperature, file_map, entry_point, created_at"
)
# The only interpolation is `_COLUMNS`, a literal a few lines up. Every value
# is a bound parameter, which is what keeps `save` safe with a `source_ref` that
# came off the command line.
_INSERT_SQL = f"""
insert into agent_builds (spec_id, iteration, parent_build_id, source_ref, created_by,
                          model, prompt_version, temperature, file_map, entry_point)
values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
returning {_COLUMNS}
"""  # noqa: S608 - the interpolated fragment is the column list above, never input
_GET_SQL = f"select {_COLUMNS} from agent_builds where id = $1"  # noqa: S608
_LATEST_SQL = (
    f"select {_COLUMNS} from agent_builds where spec_id = $1 order by iteration desc limit 1"  # noqa: S608
)
_FOR_SPEC_SQL = f"select {_COLUMNS} from agent_builds where spec_id = $1 order by iteration"  # noqa: S608


async def save(connection: asyncpg.Connection, build: Build) -> Build:
    """Register a build, allocating its iteration and linking its parent.

    Both are derived from what is already stored: the next iteration is one past
    the highest for this spec, and the parent is whatever that highest one was.
    A caller supplying either would be asserting a history the table already
    knows, and the two would drift the first time a registration was retried.
    """
    previous = await latest(connection, build.spec_id)
    row = await connection.fetchrow(
        _INSERT_SQL,
        build.spec_id,
        previous.iteration + 1 if previous else 1,
        previous.id if previous else None,
        build.source_ref,
        build.created_by,
        build.model,
        build.prompt_version,
        build.temperature,
        json.dumps(dict(build.file_map)),
        build.entry_point,
    )
    return _build(row)


async def get(connection: asyncpg.Connection, build_id: UUID) -> Build:
    """One build, or a refusal naming the id that did not resolve."""
    row = await connection.fetchrow(_GET_SQL, build_id)
    if row is None:
        raise NotFoundError(f"no build {build_id}")
    return _build(row)


async def latest(connection: asyncpg.Connection, spec_id: UUID) -> Build | None:
    """The most recent build of this spec, which is what a new one descends from."""
    row = await connection.fetchrow(_LATEST_SQL, spec_id)
    return _build(row) if row else None


async def for_spec(connection: asyncpg.Connection, spec_id: UUID) -> tuple[Build, ...]:
    """Every build of this spec, oldest first — the x axis of the curve."""
    rows = await connection.fetch(_FOR_SPEC_SQL, spec_id)
    return tuple(_build(row) for row in rows)


def _build(row: asyncpg.Record) -> Build:
    mapped = row["file_map"]
    loaded = json.loads(mapped) if isinstance(mapped, str) else dict(mapped or {})
    return Build(
        id=row["id"],
        spec_id=row["spec_id"],
        iteration=row["iteration"],
        parent_build_id=row["parent_build_id"],
        source_ref=row["source_ref"],
        created_by=row["created_by"],
        model=row["model"],
        prompt_version=row["prompt_version"],
        temperature=float(row["temperature"]) if row["temperature"] is not None else None,
        file_map={str(k): str(v) for k, v in loaded.items()},
        entry_point=row["entry_point"],
        created_at=row["created_at"],
    )
