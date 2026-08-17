"""Round-trip tests for a situation the board must account for.

The property that carries this file: a stored scenario is still *runnable*. Its
``outcomes`` has to feed straight back into ``Board.dry_run`` with no
translation, because resolving a thread means re-running the walk that raised it
— and a scenario that came back as a description rather than a program could not
be re-run at all.
"""

import asyncpg
import pytest

from meridian.core.config import settings
from meridian.domain.graph import Board
from meridian.domain.review import Scenario
from meridian.repositories import boards, scenarios
from meridian.seed import SEED, board_from_file

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(not settings().database_url, reason="DATABASE_URL is not set"),
]


ONE_COA_MISSING = Scenario(
    key="one_coa_missing",
    kind="variant",
    description="The invoice lists five batches and only four certificates arrive.",
    outcomes={"invoice_complete": "pass", "coas_valid": "missing_coa"},
    start="prealert_received",
)

RESUBMISSION = Scenario(
    key="corrected_coa_arrives",
    kind="probe",
    description="The missing certificate is sent on and the shipment is checked again.",
    outcomes={"invoice_complete": "pass", "coas_valid": ("missing_coa", "pass")},
    start="prealert_received",
)


@pytest.fixture
async def board_id(connection: asyncpg.Connection):
    return await boards.save(connection, board_from_file(SEED))


async def test_a_stored_scenario_is_still_runnable(
    connection: asyncpg.Connection, board_id, seed: Board
):
    # Not "does it come back equal" — does it still work. A scenario read from
    # the database goes straight into a dry run, which is what makes resolution
    # provable rather than declared.
    await scenarios.save(connection, board_id, ONE_COA_MISSING)
    (loaded,) = await scenarios.for_board(connection, board_id)

    walked = seed.dry_run(loaded.outcomes, start=loaded.start)
    assert walked.trace[-1].key == "report_coa_discrepancy"


async def test_an_answer_that_changes_between_visits_survives(
    connection: asyncpg.Connection, board_id
):
    # A resubmission is a check coming out one way on Tuesday and another on
    # Thursday. JSON has no tuples, so this is the round trip most likely to
    # quietly become a string or a list and stop meaning what it meant.
    await scenarios.save(connection, board_id, RESUBMISSION)
    (loaded,) = await scenarios.for_board(connection, board_id)

    assert loaded.outcomes["coas_valid"] == ("missing_coa", "pass")
    assert loaded.outcomes["invoice_complete"] == "pass"


async def test_where_a_walk_ended_up_is_recorded(connection: asyncpg.Connection, board_id):
    await scenarios.save(connection, board_id, ONE_COA_MISSING)
    assert (await scenarios.for_board(connection, board_id))[0].dryrun_result is None

    await scenarios.record_result(connection, board_id, "one_coa_missing", "dead_end")
    (loaded,) = await scenarios.for_board(connection, board_id)
    assert loaded.dryrun_result == "dead_end"


async def test_a_scenario_keeps_one_identity_across_rounds(
    connection: asyncpg.Connection, board_id
):
    # Round two re-runs the same situations against an edited board. If saving
    # duplicated them the coverage number would inflate every round.
    await scenarios.save(connection, board_id, ONE_COA_MISSING)
    await scenarios.save(connection, board_id, ONE_COA_MISSING.model_copy(update={"round": 2}))

    loaded = await scenarios.for_board(connection, board_id)
    assert len(loaded) == 1
    assert loaded[0].round == 2


async def test_a_board_with_no_scenarios_yet(connection: asyncpg.Connection, board_id):
    assert await scenarios.for_board(connection, board_id) == ()
