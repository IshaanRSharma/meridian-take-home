"""Measuring a reviewer whose output is questions.

There is no oracle for a question, so the test is run backwards: take a board
somebody would call finished, delete one thing that is known to be on it, and
check the reviewer asks for it back. That turns "does it find gaps" into a
number — of N facts removed, how many came back as questions.

Two halves, and they are worth different amounts.

The **provable** half should be perfect by construction: a field nobody filled
is a finding, a finding becomes a question, and anything less than 100% here is
a broken pipe rather than a weak reviewer. These run offline against a model
that proposes nothing, so what is measured is the deterministic path alone.

The **noticed** half is where the honest number lives, and it needs a real model
call — so it is marked and skipped by default. Recall there will be lower and
that gap is data worth reporting rather than hiding.

The no-regression test is the other side of the same coin: a finished board
should produce almost nothing. A reviewer that still finds six questions in a
board its owner signed off is one whose questions nobody will read by round
three.
"""

from dataclasses import dataclass
from typing import Any

import asyncpg
import pytest

from meridian.core.config import settings
from meridian.domain.graph import Board
from meridian.repositories import boards
from meridian.repositories import threads as threads_repo
from meridian.reviewer import run
from meridian.reviewer.distill import Distillation
from meridian.reviewer.semantic import Questions
from meridian.seed import COMPLETE, board_from_file

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(not settings().database_url, reason="DATABASE_URL is not set"),
]


@dataclass
class Turn:
    output: list[Any]
    output_parsed: Any = None


async def says_nothing(**kwargs: Any) -> Any:
    """A model that proposes no questions and settles no statements.

    Deliberately mute. What is being measured here is the deterministic path —
    a blank becomes a finding becomes a question — and a model that volunteered
    anything would make a broken pipe look like a working one.
    """
    empty = (
        Distillation(statements=[])
        if kwargs.get("text_format") is Distillation
        else Questions(questions=[])
    )
    return Turn(output=[], output_parsed=empty)


def unset(board: Board, ref: str) -> Board:
    """The board with one config field cleared, as if nobody had filled it in."""
    key, _, field = ref.partition(".")
    empty: dict[str, Any] = {"recipients": (), "captures": (), "outcomes": (), "criteria": ()}
    return board.model_copy(
        update={
            "primitives": tuple(
                card.model_copy(
                    update={"config": card.config.model_copy(update={field: empty.get(field)})}
                )
                if card.key == key
                else card
                for card in board.primitives
            )
        }
    )


def remove_edge(board: Board, key: str) -> Board:
    """The board with one line rubbed out."""
    return board.model_copy(update={"edges": tuple(e for e in board.edges if e.key != key)})


@pytest.fixture
def complete() -> Board:
    return board_from_file(COMPLETE)


async def asked_about(connection: asyncpg.Connection, board: Board) -> set[str]:
    """Every card this board gets a question about, with no model involved."""
    board_id = await boards.save(connection, board)
    result = await run.review(connection, board_id, transport=says_nothing)
    return {str(comment.primary_anchor()) for comment in result.asked}


# --- the board this is all measured against ----------------------------------


async def test_a_finished_board_is_quiet(connection: asyncpg.Connection, complete: Board):
    # The number that decides whether anyone reads round three. Deterministically
    # zero: nothing is unfilled, so nothing provable is left to ask.
    assert await asked_about(connection, complete) == set()


# --- take one fact away, and it should be asked for --------------------------


@pytest.mark.parametrize(
    ("ablation", "expected"),
    [
        ("report_coa_discrepancy.recipients", "primitive:report_coa_discrepancy"),
        ("report_invoice_discrepancy.system", "primitive:report_invoice_discrepancy"),
        ("coas_valid.on_missing_input", "primitive:coas_valid"),
        ("invoice_complete.on_missing_input", "primitive:invoice_complete"),
        ("prealert_received.correlation_key", "primitive:prealert_received"),
        ("prealert_received.match_condition", "primitive:prealert_received"),
        ("report_coa_discrepancy.idempotency_key", "primitive:report_coa_discrepancy"),
    ],
)
async def test_a_field_nobody_filled_comes_back_as_a_question(
    connection: asyncpg.Connection, complete: Board, ablation: str, expected: str
):
    assert expected in await asked_about(connection, unset(complete, ablation))


async def test_an_outcome_with_no_line_stops_the_review_rather_than_being_asked_about(
    connection: asyncpg.Connection, complete: Board
):
    # Not a question, and that is the point. Rubbing out the line for a declared
    # outcome leaves a drawing that is not a process, and a person can see it on
    # the canvas — so it is a refusal at the gate, not something to spend one of
    # six slots asking a person to answer in prose.
    board_id = await boards.save(connection, remove_edge(complete, "e6"))

    with pytest.raises(run.NotReadyForReviewError) as refused:
        await run.review(connection, board_id, transport=says_nothing)

    assert [f.field for f in refused.value.findings] == ["outcomes"]
    assert await threads_repo.for_board(connection, board_id) == ()
