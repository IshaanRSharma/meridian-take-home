"""Putting something new on the board.

Separate from editing because minting fails differently from merging. This is
where a key is invented, so it owns collision policy, and it is where two people
clicking at once actually collide.

Every call takes the board lock, reads the whole board, decides against what is
actually there, and writes the one row that changed.

**The read is what makes the write safe**, and specifically the config
validation: ``config`` is ``jsonb``, so Postgres accepts anything, and a row
that does not validate makes the **whole** board unreadable — lint, review and
freeze all go through ``boards.get``, and there is no partial read to repair it
with. One bad write bricks the board, so nothing is written that has not been
through its own model first.

Board-level validation is deliberately not in that path. ``Board`` has exactly
one validator — duplicate keys — and every call here already prevents that more
specifically and with a better message than Pydantic would give: a card checks
the key it was handed, and a line mints one that is free. Constructing a
candidate board on top of that would catch nothing and read as though it did.

A card arrives blank and that is the design. Drop it, come back to it, and every
field nobody filled is a lint finding, which is a question somebody can answer.
Only what cannot be *stored* is refused here.
"""

from typing import Literal
from uuid import UUID

import asyncpg
from pydantic import TypeAdapter

from meridian.domain.errors import ConflictingStateError, NotFoundError
from meridian.domain.graph import Board, Edge, Primitive
from meridian.domain.keys import key_for
from meridian.repositories import boards

_PRIMITIVE: TypeAdapter[Primitive] = TypeAdapter(Primitive)

PrimitiveType = Literal["event", "action", "check", "entity"]

# Edge keys are not user-facing and nothing reads meaning from them, so they are
# numbered rather than named — which is also the convention the seed already uses.
_EDGE_PREFIX = "e"

# Where a card goes when nobody said. One column, evenly spaced.
_COLUMN = 120.0
_ROW = 160.0


async def board(connection: asyncpg.Connection, name: str) -> Board:
    """An empty board, ready to be drawn on."""
    return await boards.get(connection, await boards.create(connection, name))


async def card(  # noqa: PLR0913 - a type, and four things a canvas may know at drop time
    connection: asyncpg.Connection,
    board_id: UUID,
    *,
    primitive_type: PrimitiveType,
    name: str | None = None,
    key: str | None = None,
    group_key: str | None = None,
    at: tuple[float, float] | None = None,
) -> Primitive:
    """Put a card on the board. A type is the only thing required.

    Args:
        connection: inside the caller's transaction.
        board_id: the board being drawn on.
        primitive_type: which of the four kinds of card.
        name: what the owner called it, if they have said yet. The key is
            derived from it once, here, and never regenerated when the name
            changes — thread anchors and generated filenames reference keys with
            no foreign key, so renumbering would orphan every comment pin.
        group_key: an optional region label. A label, not an entity — there is
            no `groups` table, because grouping is not a thing that has its own
            requires and provides until somebody needs one.
        key: an explicit key, for a caller that has one. Refused if taken,
            because silently returning a different one would be a lie; a
            generated key has no such expectation and is numbered instead.
        at: where it was dropped. A drawn card always ends up with a position,
            because the canvas needs one for every node — so a card created
            without one is placed below the others rather than left without.
            Refused for an Entity, which is set up rather than dragged; that
            absence is the whole implementation of "first class but not drawn".
    """
    await boards.lock_for_edit(connection, board_id)
    drawn = await boards.get(connection, board_id)
    taken = {p.key for p in drawn.primitives}

    if key is not None and key in taken:
        msg = f"a card called {key!r} is already on this board"
        raise ConflictingStateError(msg)
    if at is not None and primitive_type == "entity":
        # Refused rather than ignored. An entity is set up, not dragged, and a
        # canvas sending it a position has misunderstood something — quietly
        # dropping the value would leave that misunderstanding in place.
        msg = "an entity has no position on the canvas"
        raise ConflictingStateError(msg)
    chosen = key or key_for(name or "", taken, fallback=primitive_type)

    placed = _PRIMITIVE.validate_python(
        {
            "key": chosen,
            "primitive_type": primitive_type,
            "group_key": group_key,
            "config": {"name": name} if name else {},
        }
    )
    await boards.upsert_primitive(connection, board_id, placed)
    if primitive_type != "entity":
        await boards.set_position(connection, board_id, chosen, *(at or _free_slot(drawn)))
    return placed


async def connect(  # noqa: PLR0913 - two ends and the three things a line can carry
    connection: asyncpg.Connection,
    board_id: UUID,
    *,
    from_key: str,
    to_key: str,
    relation: Literal["normal", "exception", "repeat"] = "normal",
    on_outcomes: tuple[str, ...] | list[str] = (),
    condition: str | None = None,
) -> Edge:
    """Draw one line.

    One drag, one edge. Two outcomes sent to the same target become two edges,
    because the owner drew two lines — and `collapsed_outcomes` then asks
    whether the distinction matters, which is the question worth asking.

    An exact duplicate is a no-op returning the line already there: same ends,
    same outcomes is a double-click, never an intent.

    Both ends must exist. At creation a line into nothing can only be a client
    bug — whereas the same dangling edge left behind by a deletion is kept on
    purpose, so the owner decides whether the card or the connection was the
    mistake.
    """
    await boards.lock_for_edit(connection, board_id)
    drawn = await boards.get(connection, board_id)

    for end in (from_key, to_key):
        if not drawn.has(end):
            raise NotFoundError(end)

    outcomes = tuple(on_outcomes)
    already = next(
        (
            edge
            for edge in drawn.edges
            if (edge.from_key, edge.to_key, edge.on_outcomes) == (from_key, to_key, outcomes)
        ),
        None,
    )
    if already is not None:
        return already

    line = Edge(
        key=_next_edge_key(drawn),
        from_key=from_key,
        to_key=to_key,
        relation=relation,
        on_outcomes=outcomes,
        condition=condition,
    )
    await boards.upsert_edge(connection, board_id, line)
    return line


def _free_slot(drawn: Board) -> tuple[float, float]:
    """Somewhere to put a card nobody dropped anywhere.

    Every drawn node needs a position — the canvas cannot render one without it,
    and a missing entry means every such card lands on top of the others at the
    origin. A board authored from the command line should still open readably,
    so cards without a position stack down a single column.
    """
    below = max((position["y"] for position in drawn.layout.values()), default=-_ROW)
    return (_COLUMN, below + _ROW)


def _next_edge_key(drawn: Board) -> str:
    """``e1``, ``e2``, … — the first free number rather than a count.

    A count would reuse the key of a deleted edge, and a reused key means a
    stale canvas silently editing a line somebody else drew.
    """
    used = {edge.key for edge in drawn.edges}
    number = 1
    while f"{_EDGE_PREFIX}{number}" in used:
        number += 1
    return f"{_EDGE_PREFIX}{number}"


__all__ = ["board", "card", "connect"]
