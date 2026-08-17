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
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Query

from meridian import events
from meridian.api.dependencies import Connection

router = APIRouter(tags=["observability"])

# Four levels up from this file is the repo root: routes → api → meridian →
# src → meridian(project) → root. Resolved once at import so a bad layout fails
# loudly at startup rather than on the first request to a screen nobody opened.
_EVAL_SET = Path(__file__).resolve().parents[5] / "eval" / "expected" / "shipments.json"


@router.get("/events")
async def read_events(
    connection: Connection,
    limit: int = Query(default=200, le=1000),
) -> list[dict[str, Any]]:
    """The most recent events across every cycle, newest first.

    What an observability screen opens on before anybody has named a cycle. The
    caller groups by `cycle_id` — the rows carry it, and doing the grouping here
    would force a shape on a screen that also wants a flat feed.
    """
    return await events.recent(connection, limit)


@router.get("/events/{cycle_id}")
async def read_cycle(cycle_id: UUID, connection: Connection) -> list[dict[str, Any]]:
    """One run end to end, oldest first, which is the order it happened in."""
    return await events.for_cycle(connection, cycle_id)


def _parsed(value: object) -> dict[str, Any] | None:
    """A jsonb column as an object, whichever way the driver returned it."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None


@router.get("/evals")
async def read_evals(connection: Connection) -> dict[str, Any]:
    """Ground truth per shipment, beside whatever has actually been run.

    The comparison this returns is the product's own scoreboard: the expected
    column is what the process owner's real answers were, and the actual column
    is what a generated agent produced. Until something produces one, every
    actual is null and `runs_recorded` is zero — which is the true state and
    reads as one.
    """
    expected = json.loads(_EVAL_SET.read_text())
    shipments = expected.get("shipments", [])

    # A left join in spirit: every shipment appears whether or not it was run,
    # because a case that was never attempted is more interesting than one that
    # passed, and an inner join would hide exactly those.
    recorded = await connection.fetch(
        """
        select c.key, r.id as run_id, r.outcome, r.output, r.errored,
               r.declined, r.ended_at, r.build_id
          from eval_cases c
          left join lateral (
                select * from runs
                 where runs.case_id = c.id
                 order by started_at desc
                 limit 1
          ) r on true
        """
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
                "output": _parsed(step["output"]),
                "error": step["error"],
            }
        )
    by_key = {row["key"]: dict(row) for row in recorded}

    return {
        "unit": expected.get("unit"),
        "source": expected.get("source"),
        "runs_recorded": sum(1 for row in by_key.values() if row.get("outcome")),
        "shipments": [
            {
                "expected": shipment,
                # `runs.output` is jsonb, and asyncpg hands it back as text on a
                # connection with no codec registered. A caller that has to
                # know that is a caller reimplementing this route.
                "actual": _parsed(by_key.get(shipment["shipment_no"], {}).get("output")),
                "outcome": by_key.get(shipment["shipment_no"], {}).get("outcome"),
                # Why, not just what. An errored case with no message is a dead
                # end on screen; a failing case with no trace is a verdict
                # nobody can act on.
                "errored": by_key.get(shipment["shipment_no"], {}).get("errored"),
                "steps": steps.get(by_key.get(shipment["shipment_no"], {}).get("run_id"), []),
                "declined": json.loads(
                    by_key.get(shipment["shipment_no"], {}).get("declined") or "[]"
                ),
            }
            for shipment in shipments
        ],
    }
