"""Where the cards sit.

The one write in this package that takes no lock and validates no candidate, and
it is allowed to because a position cannot make a board invalid. No rule reads
`layout`, nothing on the frozen spec carries it, and a card at the wrong
coordinates is untidy rather than wrong. It fires on every mouse-up, and a drag
that queued behind the reviewer would be the worst thing here to make slow.

It does still read the board, for one reason: to refuse a card that is not
drawable. `jsonb_set` creates the key if it is missing, so without the check a
stale canvas would quietly accumulate positions for cards that no longer exist —
and nothing reads `layout` closely enough to ever notice.

That write is per key, so two people dragging two different cards never touch the
same value. It is the one edit on the board with no conflict to resolve.

An Entity has no position, and that absence is the whole implementation of
"first class but not drawn". Moving one is refused rather than ignored: a canvas
that tried has misunderstood something, and swallowing the call would leave the
misunderstanding in place.
"""

from collections.abc import Mapping
from uuid import UUID

import asyncpg

from meridian.domain.errors import ConflictingStateError, NotFoundError
from meridian.domain.graph import Board
from meridian.repositories import boards


async def move(
    connection: asyncpg.Connection, board_id: UUID, key: str, *, x: float, y: float
) -> None:
    """Put one card somewhere. Debounced by the caller, not by this."""
    _drawable(await boards.get(connection, board_id), key)
    await boards.set_position(connection, board_id, key, x, y)


async def rearrange(
    connection: asyncpg.Connection,
    board_id: UUID,
    positions: Mapping[str, tuple[float, float]],
) -> None:
    """Put several cards somewhere at once, for a multi-select drag.

    Still one write per card rather than one whole-layout write, so a group drag
    does not clobber a card somebody else moved at the same time. Every card is
    checked before any is moved, so a bad key in the batch moves nothing.
    """
    board = await boards.get(connection, board_id)
    for key in positions:
        _drawable(board, key)
    for key, (x, y) in positions.items():
        await boards.set_position(connection, board_id, key, x, y)


def _drawable(board: Board, key: str) -> None:
    """Refuse a card the canvas cannot be showing."""
    if not board.has(key):
        raise NotFoundError(key)
    if board.p(key).primitive_type == "entity":
        msg = "an entity has no position on the canvas"
        raise ConflictingStateError(msg)


__all__ = ["move", "rearrange"]
