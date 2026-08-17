"""Repairs: what was tried against a signature, and what came of it.

The read matters more here than the write. Without history the same failed idea
gets tried again in the next session, by a person or an agent who has no way to
know it already failed — which is the specific waste an autonomous loop does not
suffer and a human-run one does.

So `history_for` is not a report. It is a section of the failure bundle, and its
job is to stop the reader repeating an approach.
"""

from __future__ import annotations

from uuid import UUID

import asyncpg

from meridian.domain.build import Repair
from meridian.domain.errors import NotFoundError

_COLUMNS = (
    "id, build_id, classification, failure_signature, failing_case_ids, files_touched, "
    "summary, diff, status, regressed_case_ids, produced_build_id, raised_thread_id, created_at"
)
# `_COLUMNS` is the only interpolation; every value is a bound parameter.
_GET_SQL = f"select {_COLUMNS} from repairs where id = $1"  # noqa: S608
_INSERT_SQL = f"""
insert into repairs (build_id, classification, failure_signature, failing_case_ids,
                     files_touched, summary, diff, status, regressed_case_ids,
                     produced_build_id, raised_thread_id)
values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
returning {_COLUMNS}
"""  # noqa: S608 - see above


async def save(connection: asyncpg.Connection, repair: Repair) -> Repair:
    """Record one attempt.

    A `spec_gap` with no thread is refused by the table, not by this function.
    The constraint is the authority because it holds for every caller — the CLI,
    the API, and anyone with psql — where a check here would hold only for the
    ones that came through it.
    """
    row = await connection.fetchrow(
        _INSERT_SQL,
        repair.build_id,
        repair.classification,
        repair.failure_signature,
        list(repair.failing_case_ids),
        list(repair.files_touched),
        repair.summary,
        repair.diff,
        repair.status,
        list(repair.regressed_case_ids),
        repair.produced_build_id,
        repair.raised_thread_id,
    )
    return _repair(row)


async def get(connection: asyncpg.Connection, repair_id: UUID) -> Repair:
    """One attempt, or a refusal naming the id that did not resolve."""
    row = await connection.fetchrow(_GET_SQL, repair_id)
    if row is None:
        raise NotFoundError(f"no repair {repair_id}")
    return _repair(row)


async def history_for(
    connection: asyncpg.Connection, signature: str, *, spec_id: UUID | None = None
) -> tuple[Repair, ...]:
    """Everything already tried against this signature, oldest first.

    Across builds, not within one. A signature that survived three builds has
    three attempts behind it, and a history scoped to the current build would
    show none of them — which is exactly the session-to-session amnesia this
    table exists to prevent.
    """
    rows = await connection.fetch(
        "select r.* from repairs r "
        "join agent_builds b on b.id = r.build_id "
        "where r.failure_signature = $1 and ($2::uuid is null or b.spec_id = $2) "
        "order by r.created_at",
        signature,
        spec_id,
    )
    return tuple(_repair(row) for row in rows)


async def set_status(connection: asyncpg.Connection, repair_id: UUID, status: str) -> None:
    """Accept or reject an attempt.

    The gate can only reject. A human overrides it, and never approves in its
    place — which is why this takes a status rather than a verdict.
    """
    await connection.execute("update repairs set status = $2 where id = $1", repair_id, status)


def _repair(row: asyncpg.Record) -> Repair:
    return Repair(
        id=row["id"],
        build_id=row["build_id"],
        classification=row["classification"],
        failure_signature=row["failure_signature"],
        failing_case_ids=tuple(row["failing_case_ids"] or ()),
        files_touched=tuple(row["files_touched"] or ()),
        summary=row["summary"],
        diff=row["diff"],
        status=row["status"],
        regressed_case_ids=tuple(row["regressed_case_ids"] or ()),
        produced_build_id=row["produced_build_id"],
        raised_thread_id=row["raised_thread_id"],
        created_at=row["created_at"],
    )
