"""The single emit.

One table, one function, one id. `events` is the only thing the browser
subscribes to, and that is what lets a pipeline call return a `cycle_id`
immediately rather than holding a request open for the minutes a sweep takes:
the work runs headlessly and the UI watches rows arrive.

Which means the id is the whole design. Three of the seven phases are run by a
person in a terminal — a skill generates an agent, a human registers the build,
a repair is pasted and recorded — and those only join the phases a process wrote
because they carry the same `cycle_id`. Emit under a fresh one and the rows are
still true and the timeline is gone.

**A rolled-back transaction takes its events with it.** That is right for
"this never happened" and wrong for "this failed", so anything whose failure is
itself worth seeing — a case that errored, a patch the gate rejected — is
written on the transaction that *committed*, with `status = 'failed'`, rather
than left to a handler further out that may be unwinding.

Vocabularies are check constraints on the table, deliberately not enums here.
The schema is the authority; duplicating it in Python gives two places to add a
phase and one of them will be missed.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

import asyncpg

Phase = Literal["review", "compile", "codegen", "eval", "repair", "deploy", "prod"]
Status = Literal["started", "ok", "failed", "rejected"]

_INSERT_SQL = """
insert into events (cycle_id, phase, kind, status, spec_id, build_id, run_id,
                    case_key, thread_id, primitive_key, duration_ms, detail)
values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
"""


async def emit(  # noqa: PLR0913 - one row, and the phases differ in what they know
    connection: asyncpg.Connection,
    *,
    cycle_id: UUID,
    phase: Phase,
    kind: str,
    status: Status,
    spec_id: UUID | None = None,
    build_id: UUID | None = None,
    run_id: UUID | None = None,
    case_key: str | None = None,
    thread_id: UUID | None = None,
    primitive_key: str | None = None,
    duration_ms: int | None = None,
    detail: Mapping[str, Any] | None = None,
) -> None:
    """Write one row on the timeline.

    Every reference is optional because the phases genuinely differ in what they
    know: compile has a spec and no build, eval has a build and a case, repair
    has a build and a primitive. Requiring a common set would mean inventing
    values, and an invented `build_id` is worse than an absent one.
    """
    await connection.execute(
        _INSERT_SQL,
        cycle_id,
        phase,
        kind,
        status,
        spec_id,
        build_id,
        run_id,
        case_key,
        thread_id,
        primitive_key,
        duration_ms,
        json.dumps(dict(detail or {})),
    )


@asynccontextmanager
async def during(
    connection: asyncpg.Connection,
    *,
    cycle_id: UUID,
    phase: Phase,
    kind: str,
    **refs: Any,
) -> AsyncIterator[dict[str, Any]]:
    """Bracket a piece of work with a `started` row and a closing one.

    The closing row is written whether or not the body succeeded, because the
    alternative renders every failure as a phase that started and never ended —
    which a timeline shows as *still running*, the one reading that stops
    somebody looking for the cause.

    Yields a dict the body fills in. A sweep knows its score only once it has
    finished, and the score is the most useful thing in the timeline.
    """
    await emit(connection, cycle_id=cycle_id, phase=phase, kind=kind, status="started", **refs)
    detail: dict[str, Any] = {}
    started = time.monotonic()
    try:
        yield detail
    except BaseException as error:
        await emit(
            connection,
            cycle_id=cycle_id,
            phase=phase,
            kind=kind,
            status="failed",
            duration_ms=_elapsed(started),
            detail=detail | {"error": f"{type(error).__name__}: {error}"},
            **refs,
        )
        raise
    else:
        await emit(
            connection,
            cycle_id=cycle_id,
            phase=phase,
            kind=kind,
            status="ok",
            duration_ms=_elapsed(started),
            detail=detail,
            **refs,
        )


async def for_cycle(connection: asyncpg.Connection, cycle_id: UUID) -> list[dict[str, Any]]:
    """One run end to end, in the order it happened.

    Ordered by `at` and then `id`, because rows written inside one transaction
    share a timestamp — `now()` in Postgres is the transaction's start, not the
    statement's. Without the tiebreak a phase that ran in milliseconds renders in
    whatever order the planner chose, which for a timeline is simply wrong.
    """
    rows = await connection.fetch(
        "select * from events where cycle_id = $1 order by at, id", cycle_id
    )
    return [_readable(row) for row in rows]


async def recent(connection: asyncpg.Connection, limit: int = 200) -> list[dict[str, Any]]:
    """The latest rows across every cycle, newest first.

    What a screen opens on before anybody has named a cycle. Capped rather than
    paginated: this is a feed somebody glances at, and a second page of events
    is a question better asked by cycle.
    """
    rows = await connection.fetch("select * from events order by at desc, id desc limit $1", limit)
    return [_readable(row) for row in rows]


def _readable(row: asyncpg.Record) -> dict[str, Any]:
    """A row as JSON, with `detail` parsed and the numeric cast to float.

    `cost_usd` is `numeric`, which asyncpg hands back as `Decimal` — and
    `Decimal` is not JSON-serialisable, so a single priced row would 500 the
    whole feed.
    """
    out = {key: value for key, value in dict(row).items() if key != "detail"}
    return {
        **{k: (float(v) if isinstance(v, Decimal) else v) for k, v in out.items()},
        "detail": detail_of(row),
    }


def detail_of(row: asyncpg.Record | Mapping[str, Any]) -> dict[str, Any]:
    """Read a row's `detail`, whichever way asyncpg handed jsonb back.

    Without a codec registered it arrives as text on one connection and as a
    dict on another, and a caller that guessed wrong fails only on the machine
    where the guess was wrong.
    """
    value = row["detail"]
    loaded = json.loads(value) if isinstance(value, str) else value
    return dict(loaded) if isinstance(loaded, dict) else {}


def _elapsed(since: float) -> int:
    return int((time.monotonic() - since) * 1000)
