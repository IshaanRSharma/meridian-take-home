"""Scenarios: situations the board is asked to account for.

Stored rather than recomputed, and that is the whole point of the table. A
thread cites the scenario that raised it, and resolving that thread means
re-running *that* walk against the edited board — so the walk has to survive
between rounds. Regenerating an equivalent-looking scenario each round would
turn "the board now handles this" into "something like it passes", which is the
difference between resolution being proved and being asserted.

``outcomes`` is written as jsonb and comes back as exactly what ``dry_run``
takes. JSON has no tuples, so a per-visit sequence arrives as a list; Pydantic
coerces it back, and a test pins that rather than trusting it.

Keyed on ``(board_id, key)``: the same situation across rounds is one row with a
newer round, not a second row, or the coverage number inflates every time.
"""

import json
from uuid import UUID

import asyncpg

from meridian.domain.review import Scenario

_SELECT_SQL = (
    "select key, kind, description, outcomes, start_key, expected_terminal, "
    "dryrun_result, round from scenarios where board_id = $1 order by key"
)
_UPSERT_SQL = (
    "insert into scenarios (board_id, key, kind, description, outcomes, start_key, "
    "expected_terminal, round) values ($1, $2, $3, $4, $5, $6, $7, $8) "
    "on conflict (board_id, key) do update set "
    "kind = excluded.kind, description = excluded.description, outcomes = excluded.outcomes, "
    "start_key = excluded.start_key, expected_terminal = excluded.expected_terminal, "
    "round = excluded.round returning id"
)


async def for_board(connection: asyncpg.Connection, board_id: UUID) -> tuple[Scenario, ...]:
    """Every situation this board has been asked to account for."""
    rows = await connection.fetch(_SELECT_SQL, board_id)
    return tuple(_scenario(row) for row in rows)


async def save(connection: asyncpg.Connection, board_id: UUID, scenario: Scenario) -> UUID:
    """Write a scenario, replacing the same key from an earlier round.

    Deliberately does not carry ``dryrun_result`` forward. Saving describes the
    situation; running it is a separate act, and a stale result claiming a walk
    reached a terminal it no longer reaches is worse than no result at all.
    """
    scenario_id: UUID = await connection.fetchval(
        _UPSERT_SQL,
        board_id,
        scenario.key,
        scenario.kind,
        scenario.description,
        json.dumps(
            {k: list(v) if isinstance(v, tuple) else v for k, v in scenario.outcomes.items()}
        ),
        scenario.start,
        scenario.expected_terminal,
        scenario.round,
    )
    return scenario_id


async def record_result(
    connection: asyncpg.Connection, board_id: UUID, key: str, result: str
) -> None:
    """Record where the last walk ended up."""
    await connection.execute(
        "update scenarios set dryrun_result = $3 where board_id = $1 and key = $2",
        board_id,
        key,
        result,
    )


def _scenario(row: asyncpg.Record) -> Scenario:
    return Scenario(
        key=row["key"],
        kind=row["kind"],
        description=row["description"],
        outcomes=_outcomes(row["outcomes"]),
        start=row["start_key"],
        expected_terminal=row["expected_terminal"],
        dryrun_result=row["dryrun_result"],
        round=row["round"],
    )


def _outcomes(value: object) -> dict[str, str | tuple[str, ...]]:
    """Asyncpg hands back jsonb as text unless a codec is registered."""
    loaded = json.loads(value) if isinstance(value, str) else value
    if not isinstance(loaded, dict):
        return {}
    return {
        str(key): tuple(answer) if isinstance(answer, list) else str(answer)
        for key, answer in loaded.items()
    }
