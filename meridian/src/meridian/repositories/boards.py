"""Boards: rows in, ``Board`` out.

A board is always read whole. Every consumer — lint, the reviewer, the freeze —
needs the primitives and the edges together, and no query ever crosses boards,
so three statements in one round trip beat a lazy-loading scheme that would only
ever be asked for everything.

Config arrives as ``jsonb`` and is validated into the typed card configs on the
way through, so a board that loads is a board the rest of the system can trust.
"""

import json
from uuid import UUID

import asyncpg
from pydantic import TypeAdapter

from meridian.domain.errors import NotFoundError
from meridian.domain.graph import Board, Edge, Primitive

_PRIMITIVE: TypeAdapter[Primitive] = TypeAdapter(Primitive)

_BOARD_SQL = "select id, name, status, review_round, layout from boards where id = $1"
_PRIMITIVES_SQL = (
    "select key, primitive_type, group_key, config from primitives where board_id = $1 order by key"
)
_EDGES_SQL = (
    "select key, from_key, to_key, relation, on_outcomes, condition from edges "
    "where board_id = $1 order by key"
)


async def get(connection: asyncpg.Connection, board_id: UUID) -> Board:
    """The whole board, or NotFoundError."""
    row = await connection.fetchrow(_BOARD_SQL, board_id)
    if row is None:
        raise NotFoundError(str(board_id))

    primitives = await connection.fetch(_PRIMITIVES_SQL, board_id)
    edges = await connection.fetch(_EDGES_SQL, board_id)

    return Board(
        id=row["id"],
        name=row["name"],
        status=row["status"],
        review_round=row["review_round"],
        layout=_layout(row["layout"]),
        primitives=tuple(_primitive(p) for p in primitives),
        edges=tuple(_edge(e) for e in edges),
    )


async def save(connection: asyncpg.Connection, board: Board) -> UUID:
    """Write a whole board, replacing whatever was there.

    Used by the seed and by tests. Interactive edits go through the narrower
    calls below, because dragging one card must not rewrite the board.
    """
    board_id = await connection.fetchval(
        "insert into boards (id, name, status, review_round, layout) "
        "values (coalesce($1, gen_random_uuid()), $2, $3, $4, $5) "
        "on conflict (id) do update set "
        "name = excluded.name, status = excluded.status, "
        "review_round = excluded.review_round, layout = excluded.layout "
        "returning id",
        board.id,
        board.name,
        board.status,
        board.review_round,
        json.dumps(board.layout),
    )

    await connection.execute("delete from edges where board_id = $1", board_id)
    await connection.execute("delete from primitives where board_id = $1", board_id)

    await connection.executemany(
        "insert into primitives (board_id, key, primitive_type, group_key, config) "
        "values ($1, $2, $3, $4, $5)",
        [
            (
                board_id,
                p.key,
                p.primitive_type,
                p.group_key,
                json.dumps(p.config.model_dump(mode="json", exclude_none=True)),
            )
            for p in board.primitives
        ],
    )
    await connection.executemany(
        "insert into edges (board_id, key, from_key, to_key, relation, on_outcomes, condition) "
        "values ($1, $2, $3, $4, $5, $6, $7)",
        [
            (board_id, e.key, e.from_key, e.to_key, e.relation, list(e.on_outcomes), e.condition)
            for e in board.edges
        ],
    )
    return UUID(str(board_id))


async def save_layout(
    connection: asyncpg.Connection, board_id: UUID, layout: dict[str, dict[str, float]]
) -> None:
    """Write positions only.

    Dragging a card must not touch `primitives`, so that table changes when the
    process changes and not when someone tidies the canvas.
    """
    await connection.execute(
        "update boards set layout = $2 where id = $1", board_id, json.dumps(layout)
    )


async def mark_submitted(connection: asyncpg.Connection, board_id: UUID) -> None:
    """Record that this board has been frozen.

    Separate from writing the spec because it is a fact about the board, not
    about the snapshot. The freeze runs both inside one transaction, so a spec
    never exists beside a board that still claims to be in review.
    """
    await connection.execute("update boards set status = 'submitted' where id = $1", board_id)


def _primitive(row: asyncpg.Record) -> Primitive:
    return _PRIMITIVE.validate_python(
        {
            "key": row["key"],
            "primitive_type": row["primitive_type"],
            "group_key": row["group_key"],
            "config": _loads(row["config"]),
        }
    )


def _edge(row: asyncpg.Record) -> Edge:
    return Edge(
        key=row["key"],
        from_key=row["from_key"],
        to_key=row["to_key"],
        relation=row["relation"],
        on_outcomes=tuple(row["on_outcomes"]),
        condition=row["condition"],
    )


def _layout(value: object) -> dict[str, dict[str, float]]:
    """Positions, which only steps have — an entity never appears here."""
    return {
        key: {axis: float(n) for axis, n in position.items()}
        for key, position in _loads(value).items()
        if isinstance(position, dict)
    }


def _loads(value: object) -> dict[str, object]:
    """Asyncpg hands back jsonb as text unless a codec is registered."""
    if isinstance(value, str):
        loaded: dict[str, object] = json.loads(value)
        return loaded
    if isinstance(value, dict):
        return {str(k): v for k, v in value.items()}
    return {}
