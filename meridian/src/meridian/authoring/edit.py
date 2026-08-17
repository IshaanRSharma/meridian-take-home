"""Changing a card that is already there.

One function, because there is one kind of change: merge some fields onto a
card's config. Renaming is that. Picking an entity to read is that. Accepting
what a model suggested is that.

The whole module exists around a single hazard. ``model_copy(update=…)`` is the
obvious way to apply a patch to a frozen model and it **does not validate** —
`channel="carrier pigeon"`, `timeout="48 hours"`, a `recipient` typo where
`recipients` was meant, all sail straight through. And `primitives.config` is
`jsonb` with no check constraint, so Postgres takes them too. The failure lands
later, in somebody else's code path, as a `ValidationError` from ``boards.get``
— at which point the board cannot be read, cannot be linted and cannot be
repaired, because the repair would have to read it first.

So the merge goes through ``model_validate`` on the whole config, which re-runs
``extra="forbid"``, every pattern and every enum. One bad write bricks a board;
this is the line that stops it.
"""

from collections.abc import Mapping
from typing import Any
from uuid import UUID

import asyncpg

from meridian.domain.graph import Primitive
from meridian.repositories import boards


async def configure(
    connection: asyncpg.Connection,
    board_id: UUID,
    key: str,
    patch: Mapping[str, Any],
) -> Primitive:
    """Merge some fields onto one card and hand back what was stored.

    Args:
        connection: inside the caller's transaction.
        board_id: the board the card is on.
        key: which card. Never changes — a key is minted once, at creation, and
            renaming is ``configure(key, {"name": …})``. Thread anchors and
            generated filenames reference keys with no foreign key, so
            renumbering one would orphan every comment pinned to that card.
        patch: the fields to change. An absent key is left alone; an explicit
            ``None`` clears the field. A nested object replaces that object
            whole rather than merging into it, so ``{"timing": {"kind":
            "await"}}`` drops a deadline that was there — which is what a form
            submitting a whole sub-object means.

    Returns:
        The card as stored, so a form re-renders from what was actually written
        rather than from what it sent.

    Raises:
        NotFoundError: no card by that key on this board.
        ValidationError: the merged config would not load. Raised before
            anything is written, so the stored card is untouched.
    """
    await boards.lock_for_edit(connection, board_id)
    drawn = await boards.get(connection, board_id)
    card = drawn.p(key)

    current = card.config
    merged = type(current).model_validate(
        {**current.model_dump(mode="json"), **dict(patch)},
    )
    changed = card.model_copy(update={"config": merged})

    await boards.upsert_primitive(connection, board_id, changed)
    return changed


__all__ = ["configure"]
