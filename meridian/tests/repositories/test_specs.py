"""Round-trip tests for the one table nothing may edit.

Three properties, and they are the reason the table exists. A spec read back has
to still verify its own checksum, or "the board changed after you approved it"
becomes an assertion rather than a check. The column has to hold what a
generator reads and not a second encoding of it. And the database itself has to
refuse an update — a rule enforced only in Python holds until someone opens
psql.
"""

import json

import asyncpg
import pytest

from meridian.compiler import freeze
from meridian.core.config import settings
from meridian.domain.graph import Board, Edge
from meridian.domain.primitives import RoleRef
from meridian.repositories import boards, specs
from meridian.seed import SEED, board_from_file

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(not settings().database_url, reason="DATABASE_URL is not set"),
]


PATCHES = {
    "report_coa_discrepancy": {
        "recipients": (RoleRef(role="receiving_supervisor"),),
        "is_terminal": True,
    },
    "report_invoice_discrepancy": {"system": "Aurologistics WMS", "is_terminal": True},
}


@pytest.fixture
def complete() -> Board:
    """The seed board with its three documented gaps closed.

    This is what the review loop is *for*, done by hand: someone named the
    supervisor, named the system, and drew the line for `mismatched_coa`.
    """
    board = board_from_file(SEED)
    return board.model_copy(
        update={
            "primitives": tuple(
                card.model_copy(update={"config": card.config.model_copy(update=PATCHES[card.key])})
                if card.key in PATCHES
                else card
                for card in board.primitives
            ),
            "edges": (
                *board.edges,
                Edge(
                    key="e6",
                    from_key="coas_valid",
                    to_key="report_coa_discrepancy",
                    relation="exception",
                    on_outcomes=["mismatched_coa"],
                ),
            ),
        }
    )


async def test_a_spec_read_back_still_verifies_itself(
    connection: asyncpg.Connection, complete: Board
):
    board_id = await boards.save(connection, complete)
    spec = freeze.freeze(complete.model_copy(update={"id": board_id}))

    await specs.save(connection, spec)
    loaded = await specs.latest(connection, board_id)

    assert loaded is not None
    assert loaded.is_intact()
    assert loaded.checksum == spec.checksum
    assert loaded.version == 1
    assert loaded.frozen_at is not None


async def test_the_column_holds_what_a_generator_reads_and_nothing_else(
    connection: asyncpg.Connection, complete: Board
):
    # `is_intact()` rebuilds the model before re-hashing, so it would pass on any
    # encoding that round-trips — it cannot tell you the column holds the right
    # thing. This can. Storing a second serialisation would put a stale checksum
    # inside the blob beside the authoritative column, and would undo the null
    # stripping that exists so a generator reads only what someone filled in.
    board_id = await boards.save(connection, complete)
    await specs.save(connection, freeze.freeze(complete.model_copy(update={"id": board_id})))

    raw = await connection.fetchval("select payload from specs where board_id = $1", board_id)
    stored = json.loads(raw) if isinstance(raw, str) else raw

    assert "checksum" not in stored
    assert "frozen_at" not in stored
    assert _nulls(stored) == []


def _nulls(node: object) -> list[str]:
    if isinstance(node, dict):
        return [k for k, v in node.items() if v is None] + [
            found for v in node.values() for found in _nulls(v)
        ]
    if isinstance(node, list):
        return [found for item in node for found in _nulls(item)]
    return []


async def test_a_board_with_no_spec_has_no_latest(connection: asyncpg.Connection, complete: Board):
    board_id = await boards.save(connection, complete)
    assert await specs.latest(connection, board_id) is None


async def test_versions_accumulate_rather_than_replace(
    connection: asyncpg.Connection, complete: Board
):
    board_id = await boards.save(connection, complete)
    board = complete.model_copy(update={"id": board_id})

    first = freeze.freeze(board)
    await specs.save(connection, first)

    # Renaming the board would not do — a board's name is not part of its spec,
    # so the checksum would not move and the freeze would rightly refuse. The
    # change has to be one a code generator would notice.
    answered = board.model_copy(
        update={
            "primitives": tuple(
                card.model_copy(
                    update={"config": card.config.model_copy(update={"on_missing_input": "wait"})}
                )
                if card.key == "coas_valid"
                else card
                for card in board.primitives
            )
        }
    )
    second = freeze.freeze(answered, previous=first)
    await specs.save(connection, second)

    assert (await specs.latest(connection, board_id)).version == 2  # type: ignore[union-attr]
    assert (
        await connection.fetchval("select count(*) from specs where board_id = $1", board_id) == 2
    )


async def test_the_database_refuses_to_edit_a_spec(connection: asyncpg.Connection, complete: Board):
    # The immutability trigger, not a convention. A spec is what someone
    # approved, and a build claims conformance to it — an UPDATE would rewrite
    # history that other rows already point at.
    board_id = await boards.save(connection, complete)
    await specs.save(connection, freeze.freeze(complete.model_copy(update={"id": board_id})))

    # Each inside its own savepoint: the first raise aborts the surrounding
    # transaction, and without one the second statement fails for the wrong
    # reason and the test would pass on a lie.
    for statement in ("update specs set version = 99", "delete from specs"):
        savepoint = connection.transaction()
        await savepoint.start()
        with pytest.raises(asyncpg.PostgresError, match="immutable"):
            await connection.execute(statement)
        await savepoint.rollback()


async def test_freezing_marks_the_board_submitted(connection: asyncpg.Connection, complete: Board):
    board_id = await boards.save(connection, complete)
    await boards.mark_submitted(connection, board_id)
    assert (await boards.get(connection, board_id)).status == "submitted"
