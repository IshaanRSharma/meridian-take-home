"""Specs: append-only, and never read back in pieces.

The board is rows because it keeps changing. A spec is one blob because nothing
ever queries inside a frozen spec — every consumer wants the whole thing, and
the one that matters most is a code generator reading it start to finish.

``payload`` is written as the canonical form the checksum was taken over, which
is also exactly what a code generator reads: no nulls, and none of the metadata
that lives in columns. Integrity is re-established by rebuilding the model and
re-hashing rather than by comparing bytes — ``jsonb`` normalises key order and
whitespace, so the stored text is deliberately not expected to survive
byte-for-byte. That is worth stating, because it is the sort of claim a docstring
makes and nothing checks.

``version``, ``checksum`` and ``frozen_at`` are columns rather than part of the
blob: the first two are what you query and join on, the third is assigned by the
database, and a checksum embedded beside its own column is a second source of
truth waiting to go stale.
"""

import json
from uuid import UUID

import asyncpg

from meridian.domain.frozen import FrozenSpec

_INSERT_SQL = (
    "insert into specs (board_id, version, payload, checksum) "
    "values ($1, $2, $3, $4) returning id, frozen_at"
)
_LATEST_SQL = (
    "select id, board_id, version, payload, checksum, frozen_at from specs "
    "where board_id = $1 order by version desc limit 1"
)


async def save(connection: asyncpg.Connection, spec: FrozenSpec) -> UUID:
    """Write a sealed spec. Never updates — the table forbids it.

    The caller owns atomicity. Freezing also moves the board to `submitted`, and
    the two belong in one ``core.db.transaction`` so a spec can never exist
    beside a board that still says it is being drawn.
    """
    row = await connection.fetchrow(
        _INSERT_SQL, spec.board_id, spec.version, spec.payload(), spec.checksum
    )
    return UUID(str(row["id"]))


async def latest(connection: asyncpg.Connection, board_id: UUID) -> FrozenSpec | None:
    """The most recent spec frozen from this board, or None if never submitted."""
    row = await connection.fetchrow(_LATEST_SQL, board_id)
    if row is None:
        return None

    payload = row["payload"]
    body = json.loads(payload) if isinstance(payload, str) else payload
    return FrozenSpec.model_validate(
        body
        | {
            "board_id": row["board_id"],
            "version": row["version"],
            "checksum": row["checksum"],
            "frozen_at": row["frozen_at"],
        }
    )
