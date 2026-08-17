"""Round-trip tests for a settled statement.

An assertion is the only thing that crosses the freeze, so the properties that
matter are about what survives storage: the element it is about — including the
whole board, which has no key — and the machine-checkable part of a constraint,
which is the difference between a rule something can validate and prose wearing
a label.

Two of these test the database rather than the repository. That is deliberate:
the append-only rule is enforced by a trigger, and a convention nothing checks
is a convention that has already been broken somewhere.
"""

import asyncpg
import pytest

from meridian.core.config import settings
from meridian.domain.review import Anchor, Assertion
from meridian.repositories import assertions, boards
from meridian.seed import SEED, board_from_file

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(not settings().database_url, reason="DATABASE_URL is not set"),
]


SETTLED = Assertion(
    anchor=Anchor.parse("primitive:coas_valid"),
    kind="exception",
    statement="A corrected certificate re-runs every batch on the invoice.",
    round=1,
)


@pytest.fixture
async def board_id(connection: asyncpg.Connection):
    return await boards.save(connection, board_from_file(SEED))


async def test_a_statement_comes_back_as_it_went_in(
    connection: asyncpg.Connection, board_id
) -> None:
    await assertions.save(connection, board_id, SETTLED)

    stored = await assertions.for_board(connection, board_id)

    assert len(stored) == 1
    assert stored[0].anchor == SETTLED.anchor
    assert stored[0].kind == "exception"
    assert stored[0].statement == SETTLED.statement
    assert stored[0].is_active()


async def test_what_a_machine_can_check_survives_the_round_trip(
    connection: asyncpg.Connection, board_id
) -> None:
    # The whole point of `constraint_json`: a statement that can be validated
    # against a real extracted value before anything is generated. Stored as
    # prose it would be a sentence nothing can apply.
    await assertions.save(
        connection,
        board_id,
        Assertion(
            anchor=Anchor.parse("entity_field:commercial_invoice.batch_nos"),
            kind="constraint",
            statement="A batch number is seven digits.",
            constraint_json={"pattern": r"^\d{7}$"},
        ),
    )

    stored = await assertions.for_board(connection, board_id)

    assert stored[0].constraint_json == {"pattern": r"^\d{7}$"}


async def test_a_statement_about_the_whole_process_has_no_key(
    connection: asyncpg.Connection, board_id
) -> None:
    # "One container is one shipment" is about the process, not about a card,
    # and it reaches every card at the freeze. It is also the anchor that has
    # nothing to name, which is exactly where storage tends to break.
    await assertions.save(
        connection,
        board_id,
        Assertion(
            anchor=Anchor(kind="board"),
            kind="rule",
            statement="One container is one shipment.",
        ),
    )

    stored = await assertions.for_board(connection, board_id)

    assert stored[0].anchor.kind == "board"
    assert stored[0].anchor.key is None


async def test_a_statement_cannot_be_rewritten_in_place(
    connection: asyncpg.Connection, board_id
) -> None:
    # A frozen spec inlines the statements behind it. Editing one in place would
    # leave a checksummed artifact quoting text that exists nowhere, with
    # nothing to notice.
    stored_id = await assertions.save(connection, board_id, SETTLED)

    with pytest.raises(asyncpg.RaiseError):
        await connection.execute(
            "update assertions set statement = $2 where id = $1",
            stored_id,
            "Something else entirely.",
        )


async def test_a_superseded_statement_comes_back_and_says_so(
    connection: asyncpg.Connection, board_id
) -> None:
    # Superseding IS an update, and the trigger has to keep allowing it. The
    # old row stays for the audit trail and `context_for` drops it, so the read
    # path must return it rather than filter it away here.
    first = await assertions.save(connection, board_id, SETTLED)
    second = await assertions.save(
        connection,
        board_id,
        SETTLED.model_copy(
            update={"statement": "A corrected certificate re-runs only the changed batch."}
        ),
    )
    await connection.execute(
        "update assertions set superseded_by = $2 where id = $1", first, second
    )

    stored = {a.id: a for a in await assertions.for_board(connection, board_id)}

    assert stored[first].is_active() is False
    assert stored[second].is_active() is True
