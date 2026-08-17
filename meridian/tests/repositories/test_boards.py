"""Round-trip tests against a real Postgres.

Marked `db` and skipped without DATABASE_URL, so `make test` stays offline. What
these prove is the one thing unit tests cannot: that a board survives being
written as rows and read back as typed cards.

The seed board is the fixture on purpose. If the JSON, the schema and the domain
types ever disagree, this is where it shows.
"""

from uuid import UUID

import asyncpg
import pytest

from meridian.compiler import rules
from meridian.core.config import settings
from meridian.domain.errors import NotFoundError
from meridian.domain.graph import Board, EntityPrimitive
from meridian.repositories import boards
from meridian.seed import SEED, board_from_file

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(not settings().database_url, reason="DATABASE_URL is not set"),
]


@pytest.fixture
def seed_board() -> Board:
    return board_from_file(SEED)


async def test_a_board_survives_the_round_trip(connection: asyncpg.Connection, seed_board: Board):
    board_id = await boards.save(connection, seed_board)
    loaded = await boards.get(connection, board_id)

    assert loaded.name == seed_board.name
    assert {p.key for p in loaded.primitives} == {p.key for p in seed_board.primitives}
    assert {e.key for e in loaded.edges} == {e.key for e in seed_board.edges}


async def test_card_config_comes_back_typed(connection: asyncpg.Connection, seed_board: Board):
    board_id = await boards.save(connection, seed_board)
    loaded = await boards.get(connection, board_id)

    coa = loaded.p("certificate_of_analysis")
    assert isinstance(coa, EntityPrimitive)
    # The single most load-bearing fact in the process, surviving jsonb.
    assert coa.config.cardinality.kind == "one_per"
    assert str(coa.config.cardinality.per) == "commercial_invoice.line_items[].batch_no"


async def test_the_findings_are_the_same_after_a_round_trip(
    connection: asyncpg.Connection, seed_board: Board
):
    board_id = await boards.save(connection, seed_board)
    loaded = await boards.get(connection, board_id)

    before = {(f.anchor, f.field) for f in rules.findings(seed_board)}
    after = {(f.anchor, f.field) for f in rules.findings(loaded)}
    assert before == after


async def test_saving_twice_replaces_rather_than_duplicates(
    connection: asyncpg.Connection, seed_board: Board
):
    board_id = await boards.save(connection, seed_board)
    again = await boards.save(connection, seed_board.model_copy(update={"id": board_id}))
    loaded = await boards.get(connection, again)

    assert again == board_id
    assert len(loaded.primitives) == len(seed_board.primitives)


async def test_layout_writes_without_touching_the_cards(
    connection: asyncpg.Connection, seed_board: Board
):
    # Dragging a card must not rewrite `primitives`, so that table changes when
    # the process changes and not when someone tidies the canvas.
    board_id = await boards.save(connection, seed_board)
    await boards.save_layout(connection, board_id, {"coas_valid": {"x": 1.0, "y": 2.0}})
    loaded = await boards.get(connection, board_id)

    assert loaded.layout == {"coas_valid": {"x": 1.0, "y": 2.0}}
    assert len(loaded.primitives) == len(seed_board.primitives)


async def test_an_unknown_board_raises_not_found(connection: asyncpg.Connection):
    with pytest.raises(NotFoundError):
        await boards.get(connection, UUID(int=0))


async def test_entities_are_stored_beside_the_steps(
    connection: asyncpg.Connection, seed_board: Board
):
    # One table, one primitive_type column. Entities differ only in having no
    # layout entry.
    board_id = await boards.save(connection, seed_board)
    rows = await connection.fetch(
        "select primitive_type, count(*) as n from primitives "
        "where board_id = $1 group by primitive_type",
        board_id,
    )

    counts = {r["primitive_type"]: r["n"] for r in rows}
    assert counts["entity"] == 3
    assert counts["check"] == 2
