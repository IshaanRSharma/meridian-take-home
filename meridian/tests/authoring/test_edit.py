"""Changing, removing and moving what is already on the board.

The headline property is one line long: **an edit that would not load is refused
before it is written, and the stored card survives untouched.** `config` is
`jsonb` with no check constraint, so Postgres will accept `channel="carrier
pigeon"` without complaint — and `boards.get` is the only read path, used by
lint, the reviewer, the freeze and the CLI, so one bad write leaves a board that
cannot be read and therefore cannot be repaired.

The second property is what this package refuses to do for you: **an edit
changes exactly the element it names and nothing else.** No cascade on delete,
no tidying up of the cards left pointing at what went. Every orphan is already a
blocking lint finding written in a sentence a process owner can act on, and
after a review round some of those values carry a human's answer behind them.
"""

import asyncpg
import pytest
from pydantic import ValidationError

from meridian.authoring import create, delete, edit, layout
from meridian.compiler import rules
from meridian.core.config import settings
from meridian.domain.errors import ConflictingStateError, NotFoundError
from meridian.repositories import boards
from meridian.seed import COMPLETE, SEED, board_from_file

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(not settings().database_url, reason="DATABASE_URL is not set"),
]


@pytest.fixture
async def drawn(connection: asyncpg.Connection):
    return await boards.save(connection, board_from_file(SEED))


# --- changing a card ----------------------------------------------------------


async def test_a_field_can_be_set(connection: asyncpg.Connection, drawn):
    card = await edit.configure(connection, drawn, "coas_valid", {"on_missing_input": "wait"})

    assert card.config.on_missing_input == "wait"
    assert (await boards.get(connection, drawn)).p("coas_valid").config.on_missing_input == "wait"


async def test_an_edit_that_would_not_load_is_refused_before_it_is_written(
    connection: asyncpg.Connection, drawn
):
    # The headline. `model_copy(update=…)` would have accepted this, `jsonb`
    # would have stored it, and the next `boards.get` — lint, review, freeze,
    # the CLI, all of them — would raise. There is no partial read to repair a
    # board with, so the repair would need the read that no longer works.
    with pytest.raises(ValidationError):
        await edit.configure(connection, drawn, "report_coa_discrepancy", {"channel": "pigeon"})

    assert (await boards.get(connection, drawn)).p("report_coa_discrepancy").config.channel == (
        "email"
    )


async def test_a_misspelled_field_is_refused_rather_than_quietly_added(
    connection: asyncpg.Connection, drawn
):
    # `recipient` for `recipients`. `extra="forbid"` catches it — but only
    # because the merge revalidates; `model_copy` skips the config model whole.
    with pytest.raises(ValidationError):
        await edit.configure(connection, drawn, "report_coa_discrepancy", {"recipient": "the boss"})


async def test_a_malformed_duration_is_refused(connection: asyncpg.Connection, drawn):
    # "48 hours" instead of "PT48H". A pattern, not an enum, and the kind of
    # thing a model suggests and a person types.
    with pytest.raises(ValidationError):
        await edit.configure(connection, drawn, "report_coa_discrepancy", {"timeout": "48 hours"})


async def test_a_field_left_out_of_the_patch_is_left_alone(connection: asyncpg.Connection, drawn):
    await edit.configure(connection, drawn, "report_coa_discrepancy", {"system": "the WMS"})

    card = (await boards.get(connection, drawn)).p("report_coa_discrepancy")
    assert card.config.system == "the WMS"
    assert card.config.effect == "notify"


async def test_a_field_can_be_cleared_by_saying_so(connection: asyncpg.Connection, drawn):
    # An absent key means "leave it"; an explicit None means "unset it". Without
    # the difference, a form could never empty a field it had filled.
    await edit.configure(connection, drawn, "report_coa_discrepancy", {"channel": None})
    assert (await boards.get(connection, drawn)).p("report_coa_discrepancy").config.channel is None


async def test_renaming_a_card_does_not_renumber_it(connection: asyncpg.Connection, drawn):
    # Threads, assertions and generated filenames reference keys with no foreign
    # key, deliberately. A rename that renumbered would orphan every comment
    # pinned to the card, silently, one round after somebody answered it.
    card = await edit.configure(connection, drawn, "coas_valid", {"name": "Something else"})

    assert card.key == "coas_valid"
    assert card.config.name == "Something else"


async def test_configuring_a_card_that_is_not_there(connection: asyncpg.Connection, drawn):
    with pytest.raises(NotFoundError):
        await edit.configure(connection, drawn, "no_such_card", {"name": "x"})


# --- removing a card ----------------------------------------------------------


async def test_deleting_a_card_leaves_its_connections_and_says_so(
    connection: asyncpg.Connection, drawn
):
    # A cascade would delete a blocking finding somebody is supposed to see, and
    # destroy which cards were joined on which outcome — which no undo recovers
    # from a DELETE. The report is what makes offering a second click cheap.
    gone = await delete.card(connection, drawn, "coas_valid")

    assert gone.key == "coas_valid"
    assert {edge.key for edge in gone.dangling} == {"e2", "e4", "e5"}
    board = await boards.get(connection, drawn)
    assert {e.key for e in board.edges} >= {"e2", "e4", "e5"}
    assert any(f.field in ("from_key", "to_key") for f in rules.blocking(board))


async def test_deleting_a_card_does_not_edit_the_cards_that_named_it(
    connection: asyncpg.Connection, drawn
):
    # After a review round some of those values carry a human's answer behind
    # them. Removing one on a card's behalf destroys knowledge nobody asked to
    # destroy — so it is reported and left.
    gone = await delete.card(connection, drawn, "commercial_invoice")

    assert "coas_valid" in gone.still_named_by
    assert (
        "commercial_invoice" in (await boards.get(connection, drawn)).p("coas_valid").config.inputs
    )


async def test_deleting_the_row_the_process_produces_names_the_checks_that_fill_it(
    connection: asyncpg.Connection,
):
    # The report exists so an interface can offer one more click, and it went
    # quiet on the deletion where that offer is worth the most. `shipment_summary`
    # is the output row: both checks write their counts into it and neither takes
    # it as an input, so a report built from what a card *reads* concluded nobody
    # named it. The board still catches it — `fills_resolve` is blocking — but
    # only after the fact, and by then the offer is gone.
    finished = await boards.save(connection, board_from_file(COMPLETE))

    gone = await delete.card(connection, finished, "shipment_summary")

    assert gone.still_named_by == ("coas_valid", "invoice_complete")
    assert any(f.field == "fills" for f in rules.blocking(await boards.get(connection, finished)))


async def test_deleting_a_card_takes_its_position_with_it(connection: asyncpg.Connection, drawn):
    # The one orphan with no finding and no visible symptom, so nothing would
    # ever prompt anybody to tidy it.
    await delete.card(connection, drawn, "coas_valid")
    assert "coas_valid" not in (await boards.get(connection, drawn)).layout


async def test_deleting_a_card_that_is_not_there(connection: asyncpg.Connection, drawn):
    with pytest.raises(NotFoundError):
        await delete.card(connection, drawn, "no_such_card")


async def test_a_dangling_connection_can_be_removed_on_its_own(
    connection: asyncpg.Connection, drawn
):
    await delete.card(connection, drawn, "coas_valid")
    await delete.disconnect(connection, drawn, "e5")

    assert "e5" not in {e.key for e in (await boards.get(connection, drawn)).edges}


async def test_removing_a_connection_that_is_already_gone(connection: asyncpg.Connection, drawn):
    # The caller is a canvas that believed it was there. A silent success would
    # leave a line on screen that no longer exists in the board.
    await delete.disconnect(connection, drawn, "e5")
    with pytest.raises(NotFoundError):
        await delete.disconnect(connection, drawn, "e5")


# --- moving a card ------------------------------------------------------------


async def test_moving_a_card_does_not_touch_the_cards_table(connection: asyncpg.Connection, drawn):
    # Dragging must not write to `primitives`, so that table changes when the
    # process changes and not when somebody tidies the canvas.
    before = await connection.fetchval(
        "select config from primitives where board_id = $1 and key = $2", drawn, "coas_valid"
    )

    await layout.move(connection, drawn, "coas_valid", x=42.0, y=7.0)

    after = await connection.fetchval(
        "select config from primitives where board_id = $1 and key = $2", drawn, "coas_valid"
    )
    assert after == before
    assert (await boards.get(connection, drawn)).layout["coas_valid"] == {"x": 42.0, "y": 7.0}


async def test_moving_several_cards_at_once(connection: asyncpg.Connection, drawn):
    await layout.rearrange(
        connection, drawn, {"coas_valid": (10.0, 20.0), "invoice_complete": (30.0, 40.0)}
    )

    placed = (await boards.get(connection, drawn)).layout
    assert placed["coas_valid"] == {"x": 10.0, "y": 20.0}
    assert placed["invoice_complete"] == {"x": 30.0, "y": 40.0}


async def test_one_bad_key_in_a_group_drag_moves_nothing(connection: asyncpg.Connection, drawn):
    before = (await boards.get(connection, drawn)).layout

    with pytest.raises(NotFoundError):
        await layout.rearrange(
            connection, drawn, {"coas_valid": (10.0, 20.0), "ghost_card": (30.0, 40.0)}
        )

    assert (await boards.get(connection, drawn)).layout == before


async def test_an_entity_cannot_be_moved(connection: asyncpg.Connection, drawn):
    with pytest.raises(ConflictingStateError, match="no position"):
        await layout.move(connection, drawn, "commercial_invoice", x=1.0, y=2.0)


async def test_moving_a_card_that_is_not_there(connection: asyncpg.Connection, drawn):
    # `jsonb_set` creates the key if it is missing, so without this a stale
    # canvas would quietly accumulate positions for cards that no longer exist.
    with pytest.raises(NotFoundError):
        await layout.move(connection, drawn, "ghost_card", x=1.0, y=2.0)


# --- what a person actually does ----------------------------------------------


async def test_a_card_can_be_dropped_described_and_wired(connection: asyncpg.Connection):
    # The gesture the canvas performs, end to end, with no seed file involved:
    # a board, two cards, a line between them, and a position for each.
    board = await create.board(connection, "Returns")
    assert board.id is not None

    arrives = await create.card(
        connection, board.id, primitive_type="event", name="A return arrives", at=(0.0, 0.0)
    )
    checked = await create.card(
        connection, board.id, primitive_type="check", name="Is it in date", at=(0.0, 160.0)
    )
    await edit.configure(connection, board.id, checked.key, {"on_missing_input": "wait"})
    line = await create.connect(connection, board.id, from_key=arrives.key, to_key=checked.key)

    stored = await boards.get(connection, board.id)
    assert [c.key for c in stored.nodes()] == [arrives.key, checked.key]
    assert [e.key for e in stored.edges] == [line.key]
    assert stored.p(checked.key).config.on_missing_input == "wait"
    assert set(stored.layout) == {arrives.key, checked.key}
