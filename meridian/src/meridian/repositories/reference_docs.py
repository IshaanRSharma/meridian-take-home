"""Documents that describe the process, kept for the reviewer to read.

The table has existed since migration 0001 and nothing has ever written to it.
Its own comment says what it is for and what it is not: *reference documents
describe the process; reviewer input only, never in spec.* Both halves are load
bearing, and the second is why there is no path from here to a frozen spec.

``storage_path`` is where the original lives and ``extracted_text`` is what a
model reads. They are separate columns because the first is provenance a person
follows and the second is the only part any code touches.
"""

from uuid import UUID

import asyncpg

from meridian.domain.review import ReferenceDoc

_SELECT_SQL = (
    "select id, kind, filename, extracted_text from reference_docs "
    "where board_id = $1 order by filename"
)
_INSERT_SQL = (
    "insert into reference_docs (board_id, kind, filename, storage_path, extracted_text) "
    "values ($1, $2, $3, $4, $5) returning id"
)


async def for_board(connection: asyncpg.Connection, board_id: UUID) -> tuple[ReferenceDoc, ...]:
    """Every document attached to this board, in a stable order."""
    rows = await connection.fetch(_SELECT_SQL, board_id)
    return tuple(
        ReferenceDoc(
            id=row["id"],
            kind=row["kind"],
            filename=row["filename"],
            text=row["extracted_text"],
        )
        for row in rows
    )


async def save(
    connection: asyncpg.Connection, board_id: UUID, doc: ReferenceDoc, storage_path: str = ""
) -> UUID:
    """Attach a document to a board and return its id."""
    written: UUID = await connection.fetchval(
        _INSERT_SQL, board_id, doc.kind, doc.filename, storage_path or doc.filename, doc.text
    )
    return written


async def delete(connection: asyncpg.Connection, board_id: UUID, doc_id: UUID) -> bool:
    """Detach one document from one board, and say whether one went.

    Scoped by board rather than by id alone. The id is a UUID and unguessable,
    but "unguessable" is not an authorisation model — and the caller reaching this
    already named a board in the URL, so honouring it costs one predicate and
    removes a way for a document to be deleted through the wrong board.
    """
    row = await connection.fetchrow(
        "delete from reference_docs where board_id = $1 and id = $2 returning id", board_id, doc_id
    )
    return row is not None


__all__ = ["delete", "for_board", "save"]
