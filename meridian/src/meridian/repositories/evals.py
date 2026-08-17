"""The evaluation aggregate: cases, runs, their steps and their failures.

One module because they are never used apart. A sweep writes all four in one
transaction per case, and the bundle reads all four back for one signature —
splitting them would put four imports and four round trips where the work is
one thing happening once.

**One transaction for a whole sweep**, which is the caller's to open. A partial
sweep is worse than none: the gate compares two builds case by case, so a run
that stopped after three of five would be scored as a complete measurement that
happened to be missing rows. A case that raises is recorded rather than raised,
so only an infrastructure failure aborts — and re-running one is right.

`run_steps` is written from a dumped `RunTrace` rather than from a model defined
here. `CaseOutcome` already validated that shape on arrival, so a second model
would re-check the same fields under a second name — and `Step.name` becoming
`run_steps.primitive_key` is the only translation, which is the sweep's to make
because the sweep is what knows a step is named after a card.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, Literal
from uuid import UUID

import asyncpg

from meridian.domain.build import EvalCase, RunResult

Mode = Literal["sandbox", "shadow", "prod"]
Outcome = Literal["passed", "failed", "error", "running"]

_CASE_COLUMNS = "id, spec_id, key, split, origin, scenario_key, input, expected_output, tags"
# The only thing interpolated into SQL in this module is `_CASE_COLUMNS`, a
# literal defined above. Every value is a bound parameter, which is what keeps a
# case key that came off the command line harmless.
_UPSERT_CASE_SQL = f"""
insert into eval_cases (spec_id, key, split, origin, scenario_key, input, expected_output, tags)
values ($1, $2, $3, $4, $5, $6, $7, $8)
on conflict (spec_id, key) do update set
  split = excluded.split, origin = excluded.origin, scenario_key = excluded.scenario_key,
  input = excluded.input, expected_output = excluded.expected_output, tags = excluded.tags
returning {_CASE_COLUMNS}
"""  # noqa: S608 - see above


# ── cases ────────────────────────────────────────────────────────────────────


async def save_case(connection: asyncpg.Connection, spec_id: UUID, case: EvalCase) -> EvalCase:
    """Write one case, replacing an earlier load of the same key.

    Keyed on `(spec_id, key)` so re-loading a corrected fixture corrects the
    case rather than adding a second one. Two rows for one shipment would double
    its weight in every score computed afterwards.
    """
    row = await connection.fetchrow(
        _UPSERT_CASE_SQL,
        spec_id,
        case.key,
        case.split,
        case.origin,
        case.scenario_key,
        json.dumps(case.input),
        json.dumps(case.expected_output),
        list(case.tags),
    )
    return _case(row)


async def cases_for(
    connection: asyncpg.Connection,
    spec_id: UUID,
    *,
    split: str | None = None,
    keys: Sequence[str] | None = None,
) -> tuple[EvalCase, ...]:
    """The suite, or the part of it a sweep was asked for.

    `holdout` exists so a repair loop tuned against `train` can still be
    measured against something it never saw. Defaulting to everything would make
    that distinction free to ignore.
    """
    rows = await connection.fetch(
        f"select {_CASE_COLUMNS} from eval_cases where spec_id = $1 "  # noqa: S608
        "and ($2::text is null or split = $2) "
        "and ($3::text[] is null or key = any($3)) order by key",
        spec_id,
        split,
        list(keys) if keys else None,
    )
    return tuple(_case(row) for row in rows)


# ── runs ─────────────────────────────────────────────────────────────────────


async def save_run(  # noqa: PLR0913 - one row, and a run genuinely has this many facts
    connection: asyncpg.Connection,
    *,
    build_id: UUID,
    case_id: UUID | None,
    mode: Mode,
    outcome: Outcome,
    output: Mapping[str, Any],
    steps: Sequence[Mapping[str, Any]] = (),
    declined: Sequence[Mapping[str, Any]] = (),
    errored: str | None = None,
    workflow_id: str | None = None,
) -> UUID:
    """Record one case's run, with its trajectory.

    `ended_at` is set here rather than left null: the harness runs a case to
    completion or records that it did not, and a row with no end is a claim that
    something is still going.

    A case that raised produced no row, so its `output` carries the error
    instead. There is nothing for it to collide with — the comparison reads the
    columns the expected row names, and an errored case has none of them.
    """
    run_id: UUID = await connection.fetchval(
        "insert into runs (build_id, case_id, mode, outcome, output, declined, "
        "temporal_workflow_id, ended_at) values ($1, $2, $3, $4, $5, $6, $7, now()) returning id",
        build_id,
        case_id,
        mode,
        outcome,
        json.dumps(dict(output) | ({"error": errored} if errored else {})),
        json.dumps([dict(gone) for gone in declined]),
        workflow_id,
    )
    await save_steps(connection, run_id, steps)
    return run_id


async def save_steps(
    connection: asyncpg.Connection, run_id: UUID, steps: Sequence[Mapping[str, Any]]
) -> None:
    """Write the trajectory, as dumped `Step`s with `name` already renamed.

    Harness runs only. A production run leaves this empty and links out through
    `runs.temporal_workflow_id`, because Temporal owns that history and
    mirroring it would be a worse copy of a solved thing.
    """
    await connection.executemany(
        "insert into run_steps (run_id, seq, primitive_key, attempt, status, input, output, "
        "tool_calls, error, latency_ms) values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)",
        [
            (
                run_id,
                step["seq"],
                step["primitive_key"],
                step.get("attempt", 1),
                step.get("status", "ok"),
                json.dumps(step.get("input")) if step.get("input") is not None else None,
                json.dumps(step.get("output")) if step.get("output") is not None else None,
                json.dumps(step.get("tool_calls") or []),
                step.get("error"),
                step.get("latency_ms"),
            )
            for step in steps
        ],
    )


async def save_failure(  # noqa: PLR0913 - a failure names its run, its bucket and its place
    connection: asyncpg.Connection,
    run_id: UUID,
    *,
    signature: str,
    detector: str,
    primitive_key: str | None,
    detail: Mapping[str, Any],
) -> UUID:
    """Record one detected failure, bucketed by its signature."""
    failure_id: UUID = await connection.fetchval(
        "insert into failures (run_id, primitive_key, detector, signature, detail) "
        "values ($1, $2, $3, $4, $5) returning id",
        run_id,
        primitive_key,
        detector,
        signature,
        json.dumps(dict(detail)),
    )
    return failure_id


async def clear_runs(connection: asyncpg.Connection, build_id: UUID) -> int:
    """Drop a build's previous sweep so a re-run replaces rather than accumulates.

    Sweeping the same build twice is ordinary — it is how you check a flake, and
    how you re-measure after fixing the fixtures. Without this the second sweep
    doubles every count the gate and the curve are computed from.

    `run_steps` and `failures` cascade from `runs`, which is why this is one
    statement rather than three.
    """
    deleted: str = await connection.execute("delete from runs where build_id = $1", build_id)
    return int(deleted.rsplit(" ", 1)[-1])


async def results_for(connection: asyncpg.Connection, build_id: UUID) -> tuple[RunResult, ...]:
    """Every case this build ran, beside the answer it should have given.

    The join is what makes the gate a query. Comparing two builds column by
    column needs both outputs and the expected row, and all three are already
    stored — so "nothing that passed before now fails" costs a select rather
    than a second sweep of the parent.
    """
    rows = await connection.fetch(
        "select r.id as run_id, r.case_id, r.outcome, r.output, "
        "c.key as case_key, c.expected_output "
        "from runs r join eval_cases c on c.id = r.case_id "
        "where r.build_id = $1 order by c.key",
        build_id,
    )
    return tuple(_result(row) for row in rows)


def _result(row: asyncpg.Record) -> RunResult:
    output = as_json(row["output"])
    return RunResult(
        run_id=row["run_id"],
        case_id=row["case_id"],
        case_key=row["case_key"],
        outcome=row["outcome"],
        output=output,
        expected_output=as_json(row["expected_output"]),
        # Only when the run actually errored. A process whose output legitimately
        # carries a field called `error` is not an errored run, and reading it as
        # one would score every column of a working case as failed.
        errored=errored_from(row["outcome"], output),
    )


async def failures_for(
    connection: asyncpg.Connection, build_id: UUID, *, signature: str | None = None
) -> tuple[dict[str, Any], ...]:
    """Failures from a build's last sweep, worst-bucketed first.

    Ordered by how many cases share a signature, because that ordering is the
    answer to "which file do I open next" — and a bucket covering every case is
    itself evidence, since a per-check bug fails some cases and an
    infrastructure bug fails all of them the same way.
    """
    rows = await connection.fetch(
        "select f.signature, f.detector, f.primitive_key, f.detail, "
        "c.key as case_key, c.id as case_id, r.id as run_id "
        "from failures f join runs r on r.id = f.run_id "
        "join eval_cases c on c.id = r.case_id "
        "where r.build_id = $1 and ($2::text is null or f.signature = $2) "
        "order by count(*) over (partition by f.signature) desc, f.signature, c.key",
        build_id,
        signature,
    )
    return tuple(dict(row) | {"detail": as_json(row["detail"])} for row in rows)


async def trajectories_for(
    connection: asyncpg.Connection, run_ids: Sequence[UUID]
) -> dict[UUID, list[dict[str, Any]]]:
    """Every step of the named runs, keyed by run and ordered as they happened.

    One query for a set of runs rather than one per run: the bundle assembles a
    whole bucket at once, and a bucket covering every case is the common shape
    rather than the exception.
    """
    rows = await connection.fetch(
        "select run_id, seq, primitive_key, attempt, status, output, error, tool_calls "
        "from run_steps where run_id = any($1) order by run_id, seq, attempt",
        list(run_ids),
    )
    trajectories: dict[UUID, list[dict[str, Any]]] = {}
    for row in rows:
        trajectories.setdefault(row["run_id"], []).append(
            dict(row) | {"output": as_json(row["output"]), "tool_calls": as_list(row["tool_calls"])}
        )
    return trajectories


async def declines_for(
    connection: asyncpg.Connection, run_ids: Sequence[UUID]
) -> dict[UUID, list[dict[str, Any]]]:
    """What each run skipped, keyed by run."""
    rows = await connection.fetch("select id, declined from runs where id = any($1)", list(run_ids))
    return {row["id"]: as_list(row["declined"]) for row in rows}


def _case(row: asyncpg.Record) -> EvalCase:
    return EvalCase(
        id=row["id"],
        spec_id=row["spec_id"],
        key=row["key"],
        split=row["split"],
        origin=row["origin"],
        scenario_key=row["scenario_key"],
        input=as_json(row["input"]),
        expected_output=as_json(row["expected_output"]),
        tags=tuple(row["tags"] or ()),
    )


def errored_from(outcome: str | None, output: Mapping[str, Any]) -> str | None:
    """The message an errored run carried, from the output it carried it in.

    Public because the eval screen needs the same answer and a second copy is
    exactly how this breaks. Add an outcome that should also report its message
    and one copy changes while the other keeps returning None — nothing raises,
    nothing fails, the number is just wrong, and the symptom is the bug the gate
    below exists to prevent.

    There is no `runs.errored` column on purpose: the sweep writes the message
    into `output`, so a column would be a second home for a fact that has one.
    """
    if outcome != "error":
        return None
    message = output.get("error")
    return str(message) if message is not None else None


def as_json(value: object) -> dict[str, Any]:
    """Asyncpg hands jsonb back as text unless a codec is registered."""
    loaded = json.loads(value) if isinstance(value, str) else value
    return dict(loaded) if isinstance(loaded, dict) else {}


def as_list(value: object) -> list[dict[str, Any]]:
    """The same, for the jsonb columns that hold an array."""
    loaded = json.loads(value) if isinstance(value, str) else value
    if not isinstance(loaded, list):
        return []
    return [dict(item) for item in loaded if isinstance(item, dict)]
