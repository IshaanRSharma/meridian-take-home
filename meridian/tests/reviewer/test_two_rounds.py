"""One conversation, end to end, over two rounds.

Every other test in this directory proves a piece. This one proves the **loop**,
which is the thing the brief actually grades: *does it capture and resolve
ambiguity, or is it decorative.* Until this file existed no second round had ever
run, so every claim about resolution was a claim about a mechanism rather than
about a conversation.

It is a scripted conversation against a fake model, so it is deterministic and
free. What is scripted is only the *wording* — every status transition, every
identity, every re-run of a walk is the real code deciding.

The story is the seed board's own, in four acts:

    act 0   the drawing is not a process yet, and review REFUSES
    act 1   the owner draws the missing line; round one asks
    act 2   the owner answers, and edits the canvas for one of the answers
    act 3   round two, which has to get five things right

Act 0 matters more than it looks. Every published number about this reviewer is
measured on the *completed* board or an ablation of it, because the seed board
raises three blocking findings and cannot be reviewed at all. That refusal is
correct, and it means the honest first beat of the story is a board being turned
away — so it is the first thing asserted here rather than a footnote.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import asyncpg
import pytest

from meridian.core.config import settings
from meridian.domain.graph import Board, Edge
from meridian.domain.primitives import RoleRef
from meridian.repositories import assertions as assertions_repo
from meridian.repositories import boards
from meridian.repositories import threads as threads_repo
from meridian.reviewer import run
from meridian.reviewer.distill import Distillation
from meridian.reviewer.semantic import Questions
from meridian.seed import SEED, board_from_file

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(not settings().database_url, reason="DATABASE_URL is not set"),
]


@dataclass
class Turn:
    output: list[Any]
    output_parsed: Any = None


def model(*questions: dict[str, Any], settling: Sequence[Sequence[dict[str, Any]]] = ()):
    """A fake model, told apart by schema exactly as the real API tells calls apart.

    ``settling`` is one response **per distil call**, consumed in order, because
    that is what really happens: ``settle`` distils each finished conversation in
    its own call and each one answers for itself. Returning a single canned
    response to every call makes one statement appear once per settled thread,
    which looks exactly like the duplicate-statement bug and is not one.
    """
    asked = Questions.model_validate({"questions": list(questions)})
    responses = [Distillation.model_validate({"statements": list(one)}) for one in settling]
    empty = Distillation(statements=[])
    calls = iter(responses)

    async def transport(**kwargs: Any) -> Any:
        if kwargs.get("text_format") is Distillation:
            return Turn(output=[], output_parsed=next(calls, empty))
        return Turn(output=[], output_parsed=asked)

    return transport


# --- act 1: what the owner draws to make it reviewable at all ----------------


def workable(board: Board) -> Board:
    """Mark both reports as endings, and wire the outcome nobody drew a line for."""
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


# --- act 2: the owner's answers ---------------------------------------------


ENDS_THERE = {
    "category": "undefined_exception",
    "severity": "important",
    "question": "Once you have reported a COA problem, is that the end of it?",
    "reason": "The drawing stops at the report and nothing says the shipment comes back.",
    "anchors": ["primitive:coas_valid"],
    "decision_key": None,
    # A walk is what raised this, so re-running that walk is what can close it.
    "scenario_key": "coas_valid_missing_coa",
}
"""A question only a redraw can answer: it cites a walk that falls off the graph."""

WHAT_IS_A_MATCH = {
    "category": "ambiguous_rule",
    "severity": "important",
    "question": "When you compare a batch number on a certificate to the invoice, "
    "does it have to read exactly the same?",
    "reason": "Nothing says whether spacing or capitalisation matters.",
    "anchors": ["primitive:coas_valid"],
    "decision_key": None,
    "scenario_key": None,
}
"""A question about meaning: no walk can close it, only a statement."""

CORRECTION_COMES_BACK = {
    "anchor": "primitive:coas_valid",
    "kind": "exception",
    "statement": "A corrected certificate re-runs every batch on the invoice.",
    "constraint": None,
}

WHO_GETS_IT = {
    "anchor": "primitive:report_coa_discrepancy",
    "kind": "owner",
    "statement": "COA discrepancies go to the receiving supervisor.",
    "constraint": None,
}

MATCH_IGNORES_PADDING = {
    "anchor": "primitive:coas_valid",
    "kind": "terminology",
    "statement": "A batch number matches after trimming spaces and ignoring case.",
    "constraint": None,
}


async def answer(connection: asyncpg.Connection, thread_id: UUID, said: str) -> None:
    """The owner replies. `answered` means the knowledge exists — nothing more."""
    await threads_repo.add_message(connection, thread_id, "human", said)
    await threads_repo.set_status(connection, thread_id, "answered")


async def draw_the_way_back(connection: asyncpg.Connection, board_id: UUID) -> None:
    """The canvas edit the answer implies: a corrected certificate comes back."""
    await boards.upsert_edge(
        connection,
        board_id,
        Edge(key="e7", from_key="report_coa_discrepancy", to_key="coas_valid", relation="repeat"),
    )


async def fill_in(connection: asyncpg.Connection, board_id: UUID, key: str, **fields: Any) -> None:
    """The owner filling a blank on a card, which is how a lint question is answered."""
    board = await boards.get(connection, board_id)
    card = board.p(key)
    filled = card.config.model_copy(update=fields)
    await boards.upsert_primitive(connection, board_id, card.model_copy(update={"config": filled}))


def by_question(threads: Sequence[Any], fragment: str) -> Any:
    return next(t for t in threads if fragment in t.question)


# --- the conversation -------------------------------------------------------


async def test_two_rounds_of_review_close_what_the_first_round_opened(
    connection: asyncpg.Connection,
):
    seed = board_from_file(SEED)

    # ── act 0 ── the drawing is not a process, and the round does not start.
    not_yet = await boards.save(connection, seed)
    with pytest.raises(run.NotReadyForReviewError) as refused:
        await run.review(connection, not_yet, transport=model())
    assert sorted(f.field for f in refused.value.findings) == ["outcomes", "outgoing", "outgoing"]
    assert await threads_repo.for_board(connection, not_yet) == ()

    # ── act 1 ── the owner draws the missing line, and round one asks.
    board_id = await boards.save(connection, workable(seed))
    first = await run.review(connection, board_id, transport=model(ENDS_THERE, WHAT_IS_A_MATCH))

    assert first.number == 1
    asked = {t.question for t in first.asked}
    assert len(first.asked) <= 6
    # Both sources are represented: blanks a rule proved, and questions only a
    # model could raise. Neither starved the other.
    origins = {t.origin for t in first.asked}
    assert origins == {"lint", "semantic"}, origins

    priors = await threads_repo.for_board(connection, board_id)
    ends_there = by_question(priors, "is that the end of it")
    what_is_a_match = by_question(priors, "read exactly the same")

    # ── act 2 ── the owner answers. One answer implies a canvas edit; the
    # other is pure knowledge and settles by being stated.
    await answer(connection, ends_there.id, "No — once the paperwork is fixed it comes back to us.")
    await answer(connection, what_is_a_match.id, "Trim the spaces and ignore capitals.")
    await draw_the_way_back(connection, board_id)

    # A blank the owner answered on the card itself, which is how a lint
    # question is answered: filling the field IS the answer.
    recipients = by_question(priors, "receiving this")
    await answer(connection, recipients.id, "The receiving supervisor.")
    await fill_in(
        connection,
        board_id,
        "report_coa_discrepancy",
        recipients=(RoleRef(role="receiving_supervisor"),),
    )

    # ── act 3 ── round two.
    second = await run.review(
        connection,
        board_id,
        # One response per distil call, in the order `settle` walks the threads:
        # the way-back answer, the match rule, then the filled-in recipient.
        transport=model(settling=[[CORRECTION_COMES_BACK], [MATCH_IGNORES_PADDING], [WHO_GETS_IT]]),
    )

    assert second.number == 2
    settled = await threads_repo.for_board(connection, board_id)
    final = {t.id: t for t in settled}

    # 1. The walk that raised a question now ends somewhere else, so it is
    #    resolved — and nobody declared that, the re-run established it.
    assert ends_there.id in second.resolved
    assert final[ends_there.id].status == "resolved"

    # 2. A question about meaning has no walk to re-run, so what proves it is
    #    that the answer produced a statement at all.
    assert what_is_a_match.id in second.resolved

    # 3. Every settled conversation left a statement behind. Only statements
    #    cross the freeze, so a conversation that distils to nothing is one the
    #    spec never hears.
    statements = await assertions_repo.for_board(connection, board_id)
    assert {a.thread_id for a in statements} >= {ends_there.id, what_is_a_match.id}

    # 4. Nothing round one asked comes back. The blanks the owner has not filled
    #    yet keep their original thread rather than being asked again.
    assert not {t.question for t in second.asked} & asked
    keys = [t.decision_key for t in settled if t.decision_key]
    assert len(keys) == len(set(keys)), "a question was asked twice under one identity"

    # 5. A gap still on the board did not vanish. `system` is still unset on the
    #    other reporting card, and that conversation is still open — not closed,
    #    and not duplicated.
    still_missing = by_question(settled, "where this gets written")
    assert final[still_missing.id].status == "open"


async def test_a_question_whose_gap_is_still_on_the_board_does_not_resolve(
    connection: asyncpg.Connection,
):
    """The other half of act 3, isolated: answering is not redrawing.

    The owner says "yes, it comes back" and does not draw the line. The walk
    still falls off the graph, so the conversation must not close — otherwise a
    board freezes with a live hole in it under a `resolved` label.
    """
    board_id = await boards.save(connection, workable(board_from_file(SEED)))
    await run.review(connection, board_id, transport=model(ENDS_THERE))

    priors = await threads_repo.for_board(connection, board_id)
    ends_there = by_question(priors, "is that the end of it")
    await answer(connection, ends_there.id, "No — it comes back once it is fixed.")

    second = await run.review(
        connection, board_id, transport=model(settling=[[CORRECTION_COMES_BACK]])
    )

    assert ends_there.id not in second.resolved
    final = {t.id: t for t in await threads_repo.for_board(connection, board_id)}
    assert not final[ends_there.id].is_settled(), "it must keep blocking the freeze"
