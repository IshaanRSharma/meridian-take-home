"""The narrow writes, and the thing they exist instead of.

`save()` replaces a whole board: it deletes every primitive and edge row and
re-inserts them. That is right for a seed file and wrong for a person dragging
one card, and the cost is not only churn — a delete-and-reinsert resets
`created_at` and wipes `provenance`, the column that records how each field on
each card got its value. Editing one card would erase the audit trail on all of
them.

So the property that carries this file is not "the write works". It is **the
write touches one row**.
"""

import json
from uuid import uuid4

import asyncpg
import pytest

from meridian.core.config import settings
from meridian.domain.graph import Edge
from meridian.repositories import boards
from meridian.seed import SEED, board_from_file

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(not settings().database_url, reason="DATABASE_URL is not set"),
]


@pytest.fixture
async def board_id(connection: asyncpg.Connection):
    return await boards.save(connection, board_from_file(SEED))


# --- a board to draw on ------------------------------------------------------


async def test_a_new_board_is_empty_and_draft(connection: asyncpg.Connection):
    board = await boards.get(connection, await boards.create(connection, "Returns"))

    assert board.name == "Returns"
    assert board.status == "draft"
    assert board.primitives == ()
    assert board.edges == ()


# --- one card at a time ------------------------------------------------------


async def test_writing_one_card_leaves_the_others_untouched(
    connection: asyncpg.Connection, board_id
):
    # The whole reason these calls exist. `save()` would have rewritten all nine.
    before = dict(
        await connection.fetch(
            "select key, created_at from primitives where board_id = $1", board_id
        )
    )
    board = await boards.get(connection, board_id)
    renamed = board.p("coas_valid")

    await boards.upsert_primitive(
        connection,
        board_id,
        renamed.model_copy(
            update={"config": renamed.config.model_copy(update={"name": "Renamed"})}
        ),
    )

    after = dict(
        await connection.fetch(
            "select key, created_at from primitives where board_id = $1", board_id
        )
    )
    assert {k: v for k, v in after.items() if k != "coas_valid"} == {
        k: v for k, v in before.items() if k != "coas_valid"
    }
    assert (await boards.get(connection, board_id)).p("coas_valid").config.name == "Renamed"


async def test_writing_a_card_keeps_how_its_fields_were_filled_in(
    connection: asyncpg.Connection, board_id
):
    # `provenance` records whether each value was typed, clicked, filled by a
    # model or answered in review. Nothing writes it yet — but `save()` would
    # delete the row and lose it, and an edit path that quietly erased the audit
    # trail would be discovered long after the trail mattered.
    await connection.execute(
        "update primitives set provenance = $3 where board_id = $1 and key = $2",
        board_id,
        "coas_valid",
        json.dumps({"name": "typed"}),
    )
    board = await boards.get(connection, board_id)

    await boards.upsert_primitive(connection, board_id, board.p("coas_valid"))

    kept = await connection.fetchval(
        "select provenance from primitives where board_id = $1 and key = $2", board_id, "coas_valid"
    )
    assert json.loads(kept) == {"name": "typed"}


async def test_writing_a_card_twice_leaves_one_card(connection: asyncpg.Connection, board_id):
    board = await boards.get(connection, board_id)

    await boards.upsert_primitive(connection, board_id, board.p("coas_valid"))
    await boards.upsert_primitive(connection, board_id, board.p("coas_valid"))

    assert len((await boards.get(connection, board_id)).primitives) == len(board.primitives)


async def test_deleting_a_card_says_whether_one_went(connection: asyncpg.Connection, board_id):
    # The caller needs to tell "removed" from "was never there" — the second is
    # a stale canvas, and it raises rather than silently succeeding.
    assert await boards.delete_primitive(connection, board_id, "coas_valid") is True
    assert await boards.delete_primitive(connection, board_id, "coas_valid") is False


async def test_deleting_a_card_leaves_its_connections(connection: asyncpg.Connection, board_id):
    # Deliberate. The owner is the one who knows whether the card or the
    # connection was the mistake, and lint reports every orphan as blocking.
    await boards.delete_primitive(connection, board_id, "coas_valid")

    board = await boards.get(connection, board_id)
    assert not board.has("coas_valid")
    assert {e.key for e in board.edges} >= {"e4", "e5"}


# --- one connection at a time ------------------------------------------------


async def test_a_connection_comes_back_as_it_went_in(connection: asyncpg.Connection, board_id):
    await boards.upsert_edge(
        connection,
        board_id,
        Edge(
            key="e9",
            from_key="coas_valid",
            to_key="report_coa_discrepancy",
            relation="exception",
            on_outcomes=["mismatched_coa"],
        ),
    )

    stored = next(e for e in (await boards.get(connection, board_id)).edges if e.key == "e9")
    assert stored.relation == "exception"
    assert stored.on_outcomes == ("mismatched_coa",)


async def test_deleting_a_connection_says_whether_one_went(
    connection: asyncpg.Connection, board_id
):
    assert await boards.delete_edge(connection, board_id, "e5") is True
    assert await boards.delete_edge(connection, board_id, "e5") is False


# --- positions ---------------------------------------------------------------


async def test_moving_one_card_does_not_rewrite_the_layout(
    connection: asyncpg.Connection, board_id
):
    # Two people dragging two different cards never touch the same value, which
    # is what makes this the one edit on the board with no conflict to resolve.
    before = (await boards.get(connection, board_id)).layout

    await boards.set_position(connection, board_id, "coas_valid", 42.0, 7.0)

    after = (await boards.get(connection, board_id)).layout
    assert after["coas_valid"] == {"x": 42.0, "y": 7.0}
    assert {k: v for k, v in after.items() if k != "coas_valid"} == {
        k: v for k, v in before.items() if k != "coas_valid"
    }


async def test_a_card_that_never_had_a_position_can_be_given_one(
    connection: asyncpg.Connection, board_id
):
    # `jsonb_set` with create-if-missing, so the first drag of a new card works.
    await boards.set_position(connection, board_id, "prealert_email", 1.0, 2.0)
    assert (await boards.get(connection, board_id)).layout["prealert_email"] == {"x": 1.0, "y": 2.0}


async def test_forgetting_where_a_card_was(connection: asyncpg.Connection, board_id):
    await boards.clear_position(connection, board_id, "coas_valid")
    assert "coas_valid" not in (await boards.get(connection, board_id)).layout


# --- the lock ----------------------------------------------------------------


async def test_taking_the_edit_lock_does_not_change_the_board(
    connection: asyncpg.Connection, board_id
):
    # It exists to make read-modify-write serial, so the one thing it must not
    # do is have an effect of its own.
    before = await boards.get(connection, board_id)

    await boards.lock_for_edit(connection, board_id)

    assert await boards.get(connection, board_id) == before


async def test_locking_a_board_that_is_not_there_is_not_an_error(connection: asyncpg.Connection):
    # It selects nothing and returns. The caller's own `boards.get` is what
    # reports a missing board, in the domain's words rather than SQL's.
    await boards.lock_for_edit(connection, uuid4())
