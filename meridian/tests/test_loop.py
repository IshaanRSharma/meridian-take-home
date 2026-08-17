"""The claim the whole product makes, in one file.

A person draws a process. A reviewer asks what the drawing does not say. The
person answers. That answer becomes a statement, the statement reaches the one
card it is about, and a code generator reads it there.

Every link in that chain had tests before this file existed and the chain
itself had none, which is exactly how it came to be broken: `distil` was
written, tested and never called, so answers were collected and discarded and
the freeze could only ever be handed nothing. A test per link cannot catch a
missing link.

Offline, against a fake model, so it runs on every commit rather than when
someone remembers.
"""

from dataclasses import dataclass
from typing import Any

import asyncpg
import pytest

from meridian.compiler import freeze
from meridian.core.config import settings
from meridian.domain.graph import Board, Edge
from meridian.repositories import assertions as assertions_repo
from meridian.repositories import boards, specs
from meridian.repositories import threads as threads_repo
from meridian.reviewer import run
from meridian.reviewer.distill import Distillation
from meridian.reviewer.semantic import Questions
from meridian.seed import SEED, board_from_file

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(not settings().database_url, reason="DATABASE_URL is not set"),
]

ANSWER = "A corrected certificate re-runs every batch on the invoice."


@dataclass
class Turn:
    output: list[Any]
    output_parsed: Any = None


def model(*questions: dict[str, Any], settling: list[dict[str, Any]] | None = None):
    asked = Questions.model_validate({"questions": list(questions)})
    distilled = Distillation.model_validate({"statements": settling or []})

    async def transport(**kwargs: Any) -> Any:
        wanted = kwargs.get("text_format")
        return Turn(output=[], output_parsed=distilled if wanted is Distillation else asked)

    return transport


def asks_about_certificates() -> dict[str, Any]:
    return {
        "category": "undefined_exception",
        "severity": "important",
        "question": "Does a certificate problem end the process?",
        "reason": "The drawing stops there and nothing says the shipment comes back.",
        "anchors": ["primitive:coas_valid"],
        "decision_key": None,
        "scenario_key": None,
        "follows_up": None,
    }


def settles(anchor: str, statement: str = ANSWER) -> list[dict[str, Any]]:
    return [{"anchor": anchor, "kind": "exception", "statement": statement, "constraint": None}]


def workable(board: Board) -> Board:
    """The seed board with the three things that stop it being a process fixed."""
    marked = tuple(
        card.model_copy(update={"config": card.config.model_copy(update={"is_terminal": True})})
        if card.key.startswith("report_")
        else card
        for card in board.primitives
    )
    return board.model_copy(
        update={
            "primitives": marked,
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


@pytest.fixture
async def board_id(connection: asyncpg.Connection):
    return await boards.save(connection, workable(board_from_file(SEED)))


async def answer_everything(
    connection: asyncpg.Connection, board_id, anchor: str
) -> tuple[Any, ...]:
    """One round, one answer, the rest dismissed — then settle, as freezing does.

    Dismissing the blanks is what a person does with a question they have no
    answer for, and it is also the only way to reach the freeze gate: it refuses
    while anything is still open.
    """
    first = await run.review(connection, board_id, transport=model(asks_about_certificates()))
    answered = next(c for c in first.asked if c.origin == "semantic")
    assert answered.id is not None

    await threads_repo.add_message(connection, answered.id, "human", "It comes back once fixed.")
    await threads_repo.set_status(connection, answered.id, "answered")
    for other in first.asked:
        if other.id != answered.id and other.id is not None:
            await threads_repo.set_status(connection, other.id, "rejected")

    await run.settle(connection, board_id, transport=model(settling=settles(anchor)))
    return (
        await assertions_repo.for_board(connection, board_id),
        await threads_repo.for_board(connection, board_id),
    )


async def test_an_answer_reaches_the_card_it_was_about(
    connection: asyncpg.Connection, board_id
) -> None:
    settled, threads = await answer_everything(connection, board_id, "primitive:coas_valid")
    board = await boards.get(connection, board_id)

    spec = freeze.freeze(board, settled, threads)

    assert f"[exception] {ANSWER}" in spec.primitives["coas_valid"].context.local
    # And nowhere else. A statement about one card landing in another card's file
    # is how a rule about certificates ends up in the step that emails a report.
    assert all(
        ANSWER not in " ".join(card.context.local)
        for key, card in spec.primitives.items()
        if key != "coas_valid"
    )


async def test_a_statement_about_the_whole_process_reaches_every_card(
    connection: asyncpg.Connection, board_id
) -> None:
    # Anchored broadly, so it is inherited rather than local — inlined onto every
    # card, because a code generator reads one entry and joins nothing.
    settled, threads = await answer_everything(connection, board_id, "board")
    board = await boards.get(connection, board_id)

    spec = freeze.freeze(board, settled, threads)

    cards = spec.primitives.values()
    assert all(f"[exception] {ANSWER}" in card.context.inherited for card in cards)
    assert all(ANSWER not in " ".join(card.context.local) for card in cards)


async def test_the_conversation_that_produced_it_can_be_found_again(
    connection: asyncpg.Connection, board_id
) -> None:
    # Provenance is the audit path: click a statement in the spec view, land on
    # the conversation that settled it. Without a thread id on the assertion the
    # spec asserts things with no way back to who said them.
    settled, threads = await answer_everything(connection, board_id, "primitive:coas_valid")
    board = await boards.get(connection, board_id)

    spec = freeze.freeze(board, settled, threads)
    provenance = spec.primitives["coas_valid"].context.provenance
    answered = next(thread for thread in threads if thread.status == "resolved")

    assert str(answered.id) in provenance
    # Every one of them resolves, or the audit path is a dead link.
    assert set(provenance) <= {str(thread.id) for thread in threads}


async def test_the_sealed_spec_keeps_the_statement(
    connection: asyncpg.Connection, board_id
) -> None:
    # The freeze produces the object; this is the one that proves the object
    # survives the database, since `payload` is what a code generator is handed.
    settled, threads = await answer_everything(connection, board_id, "primitive:coas_valid")
    board = await boards.get(connection, board_id)

    await specs.save(connection, freeze.freeze(board, settled, threads))
    stored = await specs.latest(connection, board_id)

    assert stored is not None
    assert f"[exception] {ANSWER}" in stored.primitives["coas_valid"].context.local


async def test_a_board_with_an_unanswered_question_does_not_freeze(
    connection: asyncpg.Connection, board_id
) -> None:
    # The other half of the claim. If ambiguity could be sealed into a spec, the
    # review loop would be decoration.
    await run.review(connection, board_id, transport=model(asks_about_certificates()))
    board = await boards.get(connection, board_id)
    threads = await threads_repo.for_board(connection, board_id)

    with pytest.raises(freeze.BoardNotReadyError) as refused:
        freeze.freeze(board, (), threads)

    assert refused.value.unsettled
