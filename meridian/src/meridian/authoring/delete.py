"""Taking something off the board, and being honest about what is left.

The rule this module exists to hold:

    **An edit changes exactly the element it names and nothing else.**

So deleting a card leaves its connections dangling and leaves every other card
still naming it. That is not an oversight — `rules.edge_endpoints_exist` already
says why: *"deleting a card deliberately leaves its edges and its conversations
behind, so this is a finding rather than a cascade — the process owner decides
whether the connection or the card was the mistake."*

A cascade would delete a `blocking` finding somebody is supposed to see, and it
would destroy information no undo recovers from a `DELETE`: which two cards were
joined, on which outcome, under which condition. On a canvas a broken arrow is
*visible*, so the finding and the drawing agree. And after a review round some of
those values carry a human's answer behind them — quietly removing one on a
card's behalf destroys knowledge nobody asked to destroy.

What this does instead is **report**: `Deletion` names the orphans, so the
interface can offer *"also remove 2 connections?"* as one more click. The offer
is cheap; the decision stays the owner's.

The single exception is the card's position, which is cleared. It is the one
orphan with no finding and no visible symptom, so nothing would ever prompt
anybody to tidy it.
"""

from dataclasses import dataclass
from uuid import UUID

import asyncpg

from meridian.domain.errors import NotFoundError
from meridian.domain.graph import Edge
from meridian.repositories import boards


@dataclass(frozen=True, slots=True)
class Deletion:
    """What went, and what the board is now left holding.

    Returned rather than acted on. Every field here is already a blocking lint
    finding; this is the same information early enough to offer a second click.
    """

    key: str
    dangling: tuple[Edge, ...] = ()
    """Connections that now point at nothing, in either direction."""

    still_named_by: tuple[str, ...] = ()
    """Cards whose config still names the deleted key — `inputs`, `captures`."""


async def card(connection: asyncpg.Connection, board_id: UUID, key: str) -> Deletion:
    """Remove one card. Nothing else changes."""
    await boards.lock_for_edit(connection, board_id)
    drawn = await boards.get(connection, board_id)
    if not drawn.has(key):
        raise NotFoundError(key)

    orphaned = tuple(edge for edge in drawn.edges if key in (edge.from_key, edge.to_key))
    naming = tuple(
        other.key
        for other in drawn.primitives
        if other.key != key and key in (*other.reads(), *other.produces())
    )

    await boards.delete_primitive(connection, board_id, key)
    await boards.clear_position(connection, board_id, key)
    return Deletion(key=key, dangling=orphaned, still_named_by=naming)


async def disconnect(connection: asyncpg.Connection, board_id: UUID, key: str) -> None:
    """Remove one connection.

    Raises `NotFoundError` if it was already gone, because the caller is a
    canvas that believed it was there — and a silent success would leave a line
    on screen that no longer exists in the board.
    """
    await boards.lock_for_edit(connection, board_id)
    if not await boards.delete_edge(connection, board_id, key):
        raise NotFoundError(key)


__all__ = ["Deletion", "card", "disconnect"]
