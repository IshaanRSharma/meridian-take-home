"""Assertions: what is left of a conversation once it is settled.

The only thing that crosses the freeze. A thread records how a decision was
reached; an assertion records the decision, and generated code should read the
second one.

Append-only, enforced by a trigger rather than by this module. A frozen spec
inlines the statements behind it, so a statement edited in place would leave a
checksummed artifact quoting text that no longer exists anywhere. Round two
answers by inserting a new row and pointing the old one at it, which is why
`superseded_by` is the one column an update may touch — and why there is no
`supersede()` here yet: nothing in the review round produces one, and a function
with no caller is a guess about what the next one will need.

Superseded rows are returned rather than filtered. `context_for` already drops
them, and an audit trail you have to remember to ask for is not one.
"""

import json
from uuid import UUID

import asyncpg

from meridian.domain.review import Anchor, Assertion

_SELECT_SQL = (
    "select id, thread_id, anchor_kind, anchor_key, kind, statement, constraint_json, "
    "round, superseded_by from assertions where board_id = $1 order by round, created_at"
)
_INSERT_SQL = (
    "insert into assertions (board_id, thread_id, anchor_kind, anchor_key, kind, "
    "statement, constraint_json, round) values ($1, $2, $3, $4, $5, $6, $7, $8) returning id"
)


async def for_board(connection: asyncpg.Connection, board_id: UUID) -> tuple[Assertion, ...]:
    """Every statement ever settled about this board, oldest first."""
    rows = await connection.fetch(_SELECT_SQL, board_id)
    return tuple(_assertion(row) for row in rows)


async def save(connection: asyncpg.Connection, board_id: UUID, assertion: Assertion) -> UUID:
    """Write one settled statement. Never updates — the trigger forbids it."""
    stored: UUID = await connection.fetchval(
        _INSERT_SQL,
        board_id,
        assertion.thread_id,
        assertion.anchor.kind,
        assertion.anchor.key,
        assertion.kind,
        assertion.statement,
        json.dumps(assertion.constraint_json) if assertion.constraint_json else None,
        assertion.round,
    )
    return stored


def _assertion(row: asyncpg.Record) -> Assertion:
    return Assertion(
        id=row["id"],
        thread_id=row["thread_id"],
        anchor=Anchor(kind=row["anchor_kind"], key=row["anchor_key"]),
        kind=row["kind"],
        statement=row["statement"],
        constraint_json=_loads(row["constraint_json"]),
        round=row["round"],
        superseded_by=row["superseded_by"],
    )


def _loads(value: object) -> dict[str, object] | None:
    """Asyncpg hands back jsonb as text unless a codec is registered."""
    if value is None:
        return None
    return json.loads(value) if isinstance(value, str) else value  # type: ignore[return-value]
