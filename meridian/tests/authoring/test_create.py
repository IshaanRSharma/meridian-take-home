"""Putting things on the board.

Two properties carry this file.

**A card can be dropped with nothing but a type.** The whole authoring model is
drop-then-describe, so anything stricter turns *"I'll come back to this"* into an
error dialog. A blank card is stored, and every blank on it is a lint finding —
which is a question the reviewer will ask, not a refusal handed to somebody who
came to draw a diagram.

**What is refused is only what cannot be stored.** A self-edge that is not a
repeat is the one drawing this system genuinely cannot represent. An edge to a
card that does not exist is refused too, but for a different reason: at creation
it can only be a client bug, whereas the *same* edge left behind by a deletion is
kept on purpose so the owner decides which was the mistake.
"""

from uuid import uuid4

import asyncpg
import pytest
from pydantic import ValidationError

from meridian.authoring import create
from meridian.compiler import rules
from meridian.core.config import settings
from meridian.domain.errors import ConflictingStateError, NotFoundError
from meridian.repositories import boards
from meridian.seed import SEED, board_from_file

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(not settings().database_url, reason="DATABASE_URL is not set"),
]


@pytest.fixture
async def empty(connection: asyncpg.Connection):
    board = await create.board(connection, "Returns")
    assert board.id is not None
    return board.id


@pytest.fixture
async def drawn(connection: asyncpg.Connection):
    return await boards.save(connection, board_from_file(SEED))


# --- a board ------------------------------------------------------------------


async def test_a_new_board_has_nothing_missing(connection: asyncpg.Connection, empty):
    # Nothing has been claimed yet, so there is nothing to be missing. A board
    # that opened with a list of complaints would be the wrong first impression,
    # and it also proves `something_starts_the_process` stays quiet on nothing.
    assert rules.findings(await boards.get(connection, empty)) == []


# --- a card -------------------------------------------------------------------


async def test_a_card_can_be_dropped_with_nothing_but_a_type(connection: asyncpg.Connection, empty):
    card = await create.card(connection, empty, primitive_type="check")

    stored = await boards.get(connection, empty)
    assert stored.has(card.key)
    assert stored.p(card.key).primitive_type == "check"


async def test_a_blank_card_is_incomplete_rather_than_invalid(
    connection: asyncpg.Connection, empty
):
    # The distinction the whole package rests on. Every one of these is a
    # question somebody can answer, not an error somebody has to fix first.
    card = await create.card(connection, empty, primitive_type="check")

    board = await boards.get(connection, empty)
    fields = {f.field for f in rules.findings(board) if f.anchor == f"primitive:{card.key}"}
    assert {"name", "criteria", "outcomes"} <= fields


async def test_a_card_takes_its_key_from_what_it_was_called(connection: asyncpg.Connection, empty):
    card = await create.card(connection, empty, primitive_type="check", name="COAs valid?")
    assert card.key == "coas_valid"


async def test_an_unnamed_card_is_named_after_its_type(connection: asyncpg.Connection, empty):
    # `check`, then `check_2`. Somebody reads these in a stack trace.
    first = await create.card(connection, empty, primitive_type="check")
    second = await create.card(connection, empty, primitive_type="check")

    assert (first.key, second.key) == ("check", "check_2")


async def test_a_generated_key_that_collides_is_numbered(connection: asyncpg.Connection, empty):
    await create.card(connection, empty, primitive_type="check", name="COAs valid")
    again = await create.card(connection, empty, primitive_type="check", name="COAs valid")

    assert again.key == "coas_valid_2"


async def test_a_key_somebody_asked_for_is_refused_when_it_is_taken(
    connection: asyncpg.Connection, drawn
):
    # The asymmetry is the point. A generated key has no caller expectation to
    # violate, so it is numbered; an explicit one does, and silently handing back
    # `coas_valid_2` would be a lie.
    with pytest.raises(ConflictingStateError, match="coas_valid"):
        await create.card(connection, drawn, primitive_type="check", key="coas_valid")


async def test_a_card_can_be_dropped_where_it_was_dropped(connection: asyncpg.Connection, empty):
    card = await create.card(connection, empty, primitive_type="check", at=(120.0, 80.0))
    assert (await boards.get(connection, empty)).layout[card.key] == {"x": 120.0, "y": 80.0}


async def test_an_entity_is_never_given_a_position(connection: asyncpg.Connection, empty):
    # An entity is set up, not dragged: it has no position, and that absence is
    # the whole implementation of "first class but not drawn".
    card = await create.card(connection, empty, primitive_type="entity", name="Invoice")
    assert card.key not in (await boards.get(connection, empty)).layout


async def test_dropping_an_entity_somewhere_is_refused(connection: asyncpg.Connection, empty):
    # Refused rather than ignored. A canvas sending a position for an entity has
    # misunderstood something, and silently dropping the value would leave the
    # misunderstanding in place to be found later by somebody else.
    with pytest.raises(ConflictingStateError, match="no position"):
        await create.card(
            connection, empty, primitive_type="entity", name="Invoice", at=(10.0, 20.0)
        )


async def test_a_card_on_a_board_that_is_not_there_is_a_missing_board(
    connection: asyncpg.Connection,
):
    with pytest.raises(NotFoundError):
        await create.card(connection, uuid4(), primitive_type="check")


# --- a connection -------------------------------------------------------------


async def test_a_line_takes_the_first_free_number(connection: asyncpg.Connection, drawn):
    # The seed already has e1..e5.
    edge = await create.connect(
        connection, drawn, from_key="coas_valid", to_key="report_coa_discrepancy"
    )
    assert edge.key == "e6"


async def test_every_outcome_gets_its_own_line_out(connection: asyncpg.Connection, drawn):
    # Three outcomes, three handles, three drags. This is the ordinary case on
    # any board with a branch, not an edge case.
    await create.connect(
        connection,
        drawn,
        from_key="coas_valid",
        to_key="report_coa_discrepancy",
        relation="exception",
        on_outcomes=["mismatched_coa"],
    )

    wiring = {w.name: w.wired for w in (await boards.get(connection, drawn)).outcomes("coas_valid")}
    assert wiring == {"pass": True, "missing_coa": True, "mismatched_coa": True}


async def test_dragging_the_same_line_twice_adds_nothing(connection: asyncpg.Connection, drawn):
    # A double-click, never an intent.
    first = await create.connect(
        connection,
        drawn,
        from_key="coas_valid",
        to_key="documentation_validated",
        on_outcomes=["pass"],
    )
    again = await create.connect(
        connection,
        drawn,
        from_key="coas_valid",
        to_key="documentation_validated",
        on_outcomes=["pass"],
    )

    assert again.key == first.key
    assert len((await boards.get(connection, drawn)).edges) == len(board_from_file(SEED).edges)


async def test_two_outcomes_to_the_same_place_are_two_lines(connection: asyncpg.Connection, drawn):
    # Not one edge carrying both. The owner drew two lines, and
    # `collapsed_outcomes` then asks whether the distinction matters — which is
    # the question worth asking.
    first = await create.connect(
        connection,
        drawn,
        from_key="coas_valid",
        to_key="report_coa_discrepancy",
        on_outcomes=["mismatched_coa"],
    )
    second = await create.connect(
        connection,
        drawn,
        from_key="coas_valid",
        to_key="report_coa_discrepancy",
        on_outcomes=["missing_coa"],
    )

    assert first.key != second.key


async def test_a_line_to_a_card_that_does_not_exist_is_refused(
    connection: asyncpg.Connection, drawn
):
    # At creation it can only be a client bug. The same dangling edge left
    # behind by a deletion is kept, deliberately, and reported by lint.
    with pytest.raises(NotFoundError):
        await create.connect(connection, drawn, from_key="coas_valid", to_key="no_such_card")


async def test_a_line_from_a_card_back_to_itself_is_refused_unless_it_repeats(
    connection: asyncpg.Connection, drawn
):
    # The one drawing this system genuinely cannot represent — and the domain
    # model and the database `check` constraint agree about it.
    with pytest.raises(ValidationError):
        await create.connect(connection, drawn, from_key="coas_valid", to_key="coas_valid")

    looped = await create.connect(
        connection, drawn, from_key="coas_valid", to_key="coas_valid", relation="repeat"
    )
    assert looped.relation == "repeat"


async def test_a_line_carrying_an_outcome_the_card_never_declares_is_stored(
    connection: asyncpg.Connection, drawn
):
    # Stored, and reported blocking. Refusing it here would make the canvas
    # modal — you could not draw a line before naming the outcome — and it would
    # delete the finding that tells the owner about it.
    await create.connect(
        connection,
        drawn,
        from_key="coas_valid",
        to_key="documentation_validated",
        on_outcomes=["invented"],
    )

    board = await boards.get(connection, drawn)
    assert any(f.field == "on_outcomes" for f in rules.blocking(board))


async def test_a_card_nobody_dropped_anywhere_still_has_a_place(
    connection: asyncpg.Connection, empty
):
    # The canvas needs a position for every node it renders. Without one, a
    # board authored from the command line opens with every card stacked on the
    # origin — so cards nobody placed stack down a column instead.
    first = await create.card(connection, empty, primitive_type="event", name="It arrives")
    second = await create.card(connection, empty, primitive_type="check", name="Is it ok")

    layout = (await boards.get(connection, empty)).layout
    assert layout[first.key]["y"] < layout[second.key]["y"]
    assert layout[first.key]["x"] == layout[second.key]["x"]
