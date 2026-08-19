"""What happened, and whether it was right.

Two different questions, deliberately in one place because a screen asking one
always then asks the other. `events` is the pipeline observing itself — what ran,
in what order, how long it took. `evals` is the oracle — what the answers should
be, per shipment, from a file authored before any of this ran.

**The eval set is a file, not a table.** `eval/expected/shipments.json` is
ground truth supplied with the brief, and ground truth belongs in version control
where a diff is reviewable, not in a row somebody can UPDATE. It is read fresh
rather than cached because it changes when a human edits it, and that human is
not going to restart the API.

**Actuals are honestly absent.** Nothing writes `runs` yet — codegen and the eval
sweep are unbuilt — so `actual` is null on every row and `runs_recorded` says so
in a number. A screen that invented a plausible actual column would be lying
about the one thing this system exists to measure.
"""

import json
import re
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from meridian import events
from meridian.api.dependencies import Connection
from meridian.domain.build import Build
from meridian.healing import gym
from meridian.healing.record import repo_root
from meridian.repositories import builds as builds_repo
from meridian.repositories import specs as specs_repo
from meridian.repositories.evals import as_json, as_list, errored_from

router = APIRouter(tags=["observability"])


@router.get("/events")
async def read_events(
    connection: Connection,
    limit: int = Query(default=200, le=1000),
    board_id: UUID | None = None,
) -> list[dict[str, Any]]:
    """The most recent events across every cycle, newest first.

    What an observability screen opens on before anybody has named a cycle. The
    caller groups by `cycle_id` — the rows carry it, and doing the grouping here
    would force a shape on a screen that also wants a flat feed.

    `board_id` narrows it to one process. Without it a screen shows every board's
    cycles pooled together, which is not wrong so much as unreadable: two
    workflows' sweeps interleave by timestamp and nothing on a row says which is
    which.
    """
    return await events.recent(connection, limit, board_id)


@router.get("/events/{cycle_id}")
async def read_cycle(cycle_id: UUID, connection: Connection) -> list[dict[str, Any]]:
    """One run end to end, oldest first, which is the order it happened in."""
    return await events.for_cycle(connection, cycle_id)


@router.get("/evals")
async def read_evals(
    connection: Connection,
    board_id: UUID | None = None,
) -> dict[str, Any]:
    """Ground truth per shipment, beside whatever has actually been run.

    The comparison this returns is the product's own scoreboard: the expected
    column is what the process owner's real answers were, and the actual column
    is what a generated agent produced. Until something produces one, every
    actual is null and `runs_recorded` is zero — which is the true state and
    reads as one.
    """
    # A left join in spirit: every shipment appears whether or not it was run,
    # because a case that was never attempted is more interesting than one that
    # passed, and an inner join would hide exactly those.
    #
    # Scoped to one board's latest spec when a board is named. A suite measures
    # a *spec* — cases carry `spec_id` — so an unscoped join reports one board's
    # runs against another board's ground truth, and `runs_recorded` counts
    # across specs that never met. Unscoped is the flat feed, and only that.
    recorded = await connection.fetch(
        """
        select c.key, c.expected_output, r.id as run_id, r.outcome, r.output,
               r.declined, r.ended_at, r.build_id
          from eval_cases c
          left join lateral (
                select * from runs
                 where runs.case_id = c.id
                 order by started_at desc
                 limit 1
          ) r on true
         where $1::uuid is null
            or c.spec_id = (
                select id from specs
                 where board_id = $1::uuid
                 order by version desc
                 limit 1
            )
        """,
        board_id,
    )
    # The trajectory, so a row that says "error" can say what it was doing when
    # it did. Without this the screen reports a verdict and withholds the only
    # thing that explains it, which sends the reader to a terminal.
    trails = await connection.fetch(
        """
        select run_id, seq, primitive_key, status, output, error
          from run_steps
         where run_id = any($1::uuid[])
         order by run_id, seq
        """,
        [row["run_id"] for row in recorded if row["run_id"]],
    )
    steps: dict[Any, list[dict[str, Any]]] = {}
    for step in trails:
        steps.setdefault(step["run_id"], []).append(
            {
                "seq": step["seq"],
                "step": step["primitive_key"],
                "status": step["status"],
                "output": as_json(step["output"]) or None,
                "error": step["error"],
            }
        )

    return {
        "unit": "one row per shipment, not per email and not per invoice",
        "source": f"{len(recorded)} case(s) loaded against this spec",
        "runs_recorded": sum(1 for row in recorded if row["outcome"]),
        "shipments": [
            {
                "expected": {"shipment_no": row["key"], **as_json(row["expected_output"])},
                # `runs.output` is jsonb, and asyncpg hands it back as text on a
                # connection with no codec registered. A caller that has to
                # know that is a caller reimplementing this route.
                "actual": as_json(row["output"]) or None,
                "outcome": row["outcome"],
                # Why, not just what. An errored case with no message is a dead
                # end on screen; a failing case with no trace is a verdict
                # nobody can act on.
                "errored": errored_from(row["outcome"], as_json(row["output"])),
                "steps": steps.get(row["run_id"], []),
                "declined": as_list(row["declined"]),
            }
            for row in recorded
        ],
    }


@router.get("/boards/{board_id}/gym")
async def read_gym(
    board_id: UUID,
    connection: Connection,
    build: int | None = Query(default=None, description="which iteration; the latest by default"),
) -> dict[str, Any]:
    """The healing loop as a training run, in one read.

    One endpoint rather than five, because these are one screen and they come
    from two tables. A page that fetched the heatmap, the curve, the attempts
    and the episode length separately would issue four round trips for four
    views of the same two queries, and would render them at four different
    moments — so the curve could disagree with the heatmap above it while both
    were still loading.

    **Nothing is computed here.** `gym.observe` and `gym.episode` already know
    what a cell is worth and which columns no card fills; this route shapes
    them for JSON and stops. A second implementation of `reachable` in a route
    handler is exactly how a screen starts disagreeing with the CLI about
    whether an agent has converged.
    """
    spec = await specs_repo.latest(connection, board_id)
    spec_id = await specs_repo.latest_id(connection, board_id)
    if spec is None or spec_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="nothing frozen yet")

    registered = await builds_repo.for_spec(connection, spec_id)
    chosen = await _chosen(connection, spec_id, build)
    if chosen is None:
        # Two different absences, and telling them apart is the difference
        # between "run codegen" and "you typed the wrong number".
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"no build {build} for this spec; registered: "
                f"{', '.join(str(one.iteration) for one in registered)}"
                if registered
                else "nothing registered yet — `meridian build register` first"
            ),
        )

    board = await gym.observe(connection, build=chosen, spec=spec, spec_id=spec_id)
    run = await gym.episode(connection, spec=spec, spec_id=spec_id)
    agreed, measured, reachable = board.score()

    return {
        "build": {
            "iteration": chosen.iteration,
            "source_ref": chosen.source_ref,
            "created_by": chosen.created_by,
            "model": chosen.model,
        },
        "builds": [one.iteration for one in registered],
        "cases": list(board.cases()),
        "columns": list(board.columns()),
        # Flat, not nested by case. A sparse grid is the honest shape — a case
        # the suite measured on fewer columns has fewer cells, and a nested map
        # would have to invent a value for the ones it never had.
        "cells": [
            {
                "case": cell.case,
                "column": cell.column,
                "state": cell.state(),
                "expected": cell.expected,
                "actual": cell.actual,
                # How close, beside whether it agreed. A binary cell cannot tell
                # 13-of-14 from 0-of-14, and those are entirely different states
                # of the same agent — which is exactly what "is it converging"
                # is asking. Never scores; only shades.
                "nearness": round(cell.nearness(), 3),
            }
            for cell in board.cells
        ],
        "splits": dict(board.splits),
        "errored": dict(board.errored),
        "blocked": list(board.blocked()),
        "green": list(board.green()),
        "score": {
            "agreed": agreed,
            "measured": measured,
            "reachable": reachable,
            "nearness": round(board.nearness(), 3),
        },
        "terminated": board.terminated(),
        # `cases` travels with every point and is not decoration. A build is
        # swept on whatever set somebody ran at the time, so a point measured on
        # two cases and one measured on ten have different denominators — joined
        # by a line they draw a collapse that never happened. The screen needs
        # the count to break the line.
        "curve": [
            {"iteration": i, "agreed": a, "measured": m, "reachable": r, "cases": n}
            for i, a, m, r, n in run.curve()
        ],
        "attempts": {key: list(value) for key, value in run.attempts.items()},
        "resisted": list(run.resisted()),
        "steps_to_green": run.steps_to_green(),
        "beliefs": _beliefs(chosen, await _recorded(connection, spec_id)),
    }


async def _chosen(connection: Connection, spec_id: UUID, iteration: int | None) -> Build | None:
    """The build a caller asked for, or the most recent one.

    Iterations rather than ids on the wire, for the same reason the CLI takes
    them: an iteration is the number somebody reads off the curve, and a uuid is
    something they would have to look up first.
    """
    if iteration is None:
        return await builds_repo.latest(connection, spec_id)
    for built in await builds_repo.for_spec(connection, spec_id):
        if built.iteration == iteration:
            return built
    return None


async def _recorded(connection: Connection, spec_id: UUID) -> str:
    """Everything every repair against this spec said, as one searchable blob.

    Summaries rather than signatures, and that distinction is the whole point:
    a signature names the column that was wrong, while the summary is where the
    repair skill asks somebody to say *which assumption this falsified*. Only
    the second can retire a belief.
    """
    rows = await connection.fetch(
        "select r.summary, r.failure_signature from repairs r "
        "join agent_builds b on b.id = r.build_id where b.spec_id = $1",
        spec_id,
    )
    return " ".join(f"{row['summary']} {row['failure_signature']}" for row in rows)


def _beliefs(build: Build, recorded: str) -> list[dict[str, Any]]:
    """What the generator decided the spec did not, and whether it still holds.

    Every entry carries a `falsified_if` — a prediction about the failure that
    would disprove it — which is the part that makes this a scientific record
    rather than a changelog. Surfacing it beside the score is what lets somebody
    watch a belief die.

    **`falsified` is computed, never guessed.** A prediction written in prose
    cannot be matched against a scoreboard by machine, so the only honest signal
    is a repair that *named* the assumption when it was recorded — which the
    repair skill asks for explicitly. An assumption nothing has named is
    reported as standing, not as true.

    Matched on word boundaries rather than as a bare substring, because these
    ids nest: `batch_matching` sits inside `batch_matching_is_exact`, and a
    plain `in` would retire a belief nobody tested every time its longer
    neighbour was disproved. Striking through the wrong belief is worse than
    striking through none.
    """
    root = repo_root(Path.cwd())
    if root is None:
        return []
    try:
        body = json.loads((root / "agents" / build.slug() / "assumptions.json").read_text())
    except (OSError, json.JSONDecodeError):
        # A build that recorded none is a worse build, not a broken one.
        return []

    return [
        {
            "id": entry.get("id"),
            "decision": entry.get("decision"),
            "because": entry.get("because"),
            "prompted_by": entry.get("prompted_by"),
            "falsified_if": entry.get("falsified_if"),
            "falsified": _named(str(entry.get("id") or ""), recorded),
        }
        for entry in body.get("assumptions") or []
    ]


def _named(assumption: str, recorded: str) -> bool:
    """Whether a repair mentioned this assumption by id, and not merely near it."""
    if not assumption:
        return False
    return re.search(rf"\b{re.escape(assumption)}\b", recorded) is not None


# ── what the agent made of mail nobody has scored ────────────────────────────

CHECKED = "total"
"""What marks a trace step as a check rather than an action.

Recognised by carrying a count, never by a list of names. A board that grows a
third check would otherwise need this route edited before its runs could be
read, and nobody would find out until a screen quietly under-reported.
"""


@router.get("/boards/{board_id}/triggered")
async def read_triggered(
    board_id: UUID,
    connection: Connection,
    limit: int = Query(default=25, le=200),
) -> dict[str, Any]:
    """Runs against mail that arrived, where there is no answer to compare to.

    A different question from the scoreboard above it, and it must not be
    rendered as though it were the same one. The eval suite asks *was this
    right*; this asks *what did it say* about a shipment nobody has scored — so
    there is no score here, and a screen that showed one would be inventing the
    single thing this system exists to measure.

    Read from `runs` where `case_id is null`, which is what the schema means by
    *null = prod*. Nothing is recomputed that the trigger already decided about
    a run's outcome; what IS derived here is the four gates, and deliberately —
    see `_gates`.
    """
    spec_id = await specs_repo.latest_id(connection, board_id)
    if spec_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="nothing frozen yet")

    rows = await connection.fetch(
        """
        select r.id as run_id, r.outcome, r.output, r.declined, r.started_at, r.ended_at,
               b.iteration
          from runs r
          join agent_builds b on b.id = r.build_id
         where b.spec_id = $1 and r.case_id is null
         order by r.started_at desc
         limit $2
        """,
        spec_id,
        limit,
    )
    trails = await connection.fetch(
        """
        select run_id, seq, primitive_key, status, output, error
          from run_steps
         where run_id = any($1::uuid[])
         order by run_id, seq
        """,
        [row["run_id"] for row in rows],
    )
    steps: dict[Any, list[dict[str, Any]]] = {}
    for step in trails:
        steps.setdefault(step["run_id"], []).append(
            {
                "seq": step["seq"],
                "step": step["primitive_key"],
                "status": step["status"],
                "output": as_json(step["output"]) or None,
                "error": step["error"],
            }
        )

    return {
        "asks": "what the agent said about mail nobody has scored — never whether it was right",
        "runs": [_triggered(row, steps.get(row["run_id"], [])) for row in rows],
    }


def _triggered(row: Any, trail: list[dict[str, Any]]) -> dict[str, Any]:
    """One triggered run, split by whether the agent could key it at all.

    `needs_correlation` is a **result**, not an error. The message is a real
    pre-alert, it was read, and the process model has no rule for keying an air
    waybill — so the agent declined to guess a shipment rather than inventing a
    unit of work nobody approved. Filing that under the same red as a crash
    would send somebody to debug code that behaved correctly, and would bury the
    one thing on this screen that belongs to the process owner.
    """
    produced = as_json(row["output"])
    declined = as_list(row["declined"])

    if produced.get("state") == "needs_correlation":
        return {
            "run_id": str(row["run_id"]),
            "build": row["iteration"],
            "at": row["started_at"].isoformat() if row["started_at"] else None,
            "state": "needs_correlation",
            "shipment": None,
            "finding": {
                "subject": produced.get("subject"),
                "sender": produced.get("sender"),
                "received_at": produced.get("received_at"),
                "attachments": produced.get("attachments") or [],
                "reason": produced.get("reason"),
            },
            "row": {},
            "gates": [],
            "trustworthy": None,
            "declined": declined,
            "steps": trail,
        }

    gates = _gates(trail, declined)
    return {
        "run_id": str(row["run_id"]),
        "build": row["iteration"],
        "at": row["started_at"].isoformat() if row["started_at"] else None,
        "state": "processed",
        "shipment": produced.get("shipment_no"),
        "finding": None,
        # What it is TRYING to produce, field by field. The shipment number is
        # how the row was filed rather than something a check computed, so it is
        # shown as the heading and not as one of the values.
        "row": {k: v for k, v in produced.items() if k != "shipment_no"},
        "gates": gates,
        # Every gate that is about the run itself. `declined` is reported beside
        # them and does not vote: attachments nobody read do not make the
        # arithmetic wrong, they make "what arrived" unreliable, and conflating
        # the two would fail a correct row for reading a signature image.
        "trustworthy": all(gate["held"] for gate in gates if gate["counts"]),
        "declined": declined,
        "steps": trail,
    }


def _gates(trail: list[dict[str, Any]], declined: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The four properties a row can be judged on with no answer to check it against.

    **Derived from the stored trajectory, not copied from the run.** The trigger
    computes the same four before deciding an outcome, and storing its verdict
    separately would be a second truth about one run — the first time a trace was
    backfilled or a step dropped, the two would disagree and nothing would say
    which was right. The trace is the evidence; this reads it.

    A check is a step carrying a count. Reading them by name would need this
    function edited every time a board grows one.
    """
    counted = [
        step
        for step in trail
        if isinstance(step.get("output"), dict) and CHECKED in step["output"]
    ]
    examined = [step["output"] for step in counted]

    reached = bool(counted)
    every = all(_as_int(one.get(CHECKED)) > 0 for one in examined) if examined else False
    holds = all(
        _as_int(one.get("passed")) + _as_int(one.get("failed")) == _as_int(one.get(CHECKED))
        for one in examined
    )
    return [
        {
            "name": "reached a check",
            "held": reached,
            "counts": True,
            "says": "a check ran"
            if reached
            else "no check ran — nothing reached the part that decides",
        },
        {
            "name": "examined something",
            "held": every,
            "counts": True,
            "says": f"{len(counted)} check(s) looked at rows"
            if every
            else "a check examined zero rows, so it agreed with nothing",
        },
        {
            "name": "counts reconcile",
            "held": holds,
            "counts": True,
            "says": "passed and failed sum to total"
            if holds
            else "a check's passed and failed do not sum to its total",
        },
        {
            "name": "everything was read",
            # Reported, and deliberately does not vote — see `_triggered`.
            "counts": False,
            "held": not declined,
            "says": "every attachment was read"
            if not declined
            else f"{len(declined)} attachment(s) were not read",
        },
    ]


def _as_int(value: Any) -> int:
    """A count from a trace, where a missing one is zero rather than a crash."""
    return value if isinstance(value, int) and not isinstance(value, bool) else 0
