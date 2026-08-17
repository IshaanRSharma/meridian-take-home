"""Tests for one round of review.

This is the file that answers the question the brief actually asks — whether the
loop captures and resolves ambiguity or is decorative. Every test here runs
offline against a fake model, so the loop is exercised deterministically and for
free; whether the *questions* are any good is a separate matter, measured by
hand and by ablation.

Three properties carry it. A drawing that does not work as a process never
reaches review. A question already dealt with never comes back. And `resolved`
is earned by re-running the walk that raised the question, not by anyone saying
so.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import asyncpg
import pytest

from meridian.core.config import settings
from meridian.domain.errors import IncompleteError
from meridian.domain.graph import Board, Edge
from meridian.domain.review import Anchor, Evidence, Thread
from meridian.repositories import assertions as assertions_repo
from meridian.repositories import boards
from meridian.repositories import scenarios as scenarios_repo
from meridian.repositories import threads as threads_repo
from meridian.reviewer import ranking, run
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


def model_asking(*questions: dict[str, Any], settling: Sequence[dict[str, Any]] = ()):
    """A fake model: proposes `questions` when reviewing, `settling` when distilling.

    One round now makes two kinds of call, and they are told apart the way the
    real API tells them apart — by the schema the answer has to satisfy.
    """
    asked = Questions.model_validate({"questions": list(questions)})
    distilled = Distillation.model_validate({"statements": list(settling)})

    async def transport(**kwargs: Any) -> Any:
        wanted = kwargs.get("text_format")
        return Turn(output=[], output_parsed=distilled if wanted is Distillation else asked)

    return transport


def statement(**overrides: Any) -> dict[str, Any]:
    return {
        "anchor": "primitive:coas_valid",
        "kind": "exception",
        "statement": "A corrected certificate re-runs every batch on the invoice.",
        "constraint": None,
    } | overrides


def proposed(**overrides: Any) -> dict[str, Any]:
    return {
        "category": "undefined_exception",
        "severity": "important",
        "question": "Does a COA problem end the process?",
        "reason": "The drawing stops there and nothing says the shipment comes back.",
        "anchors": ["primitive:coas_valid"],
        "decision_key": None,
        "scenario_key": "coas_valid_missing_coa",
    } | overrides


def workable(board: Board) -> Board:
    """The seed board with the three things that stop it being a process fixed.

    Exactly what a process owner does on the canvas in ten seconds: mark both
    reports as endings, and draw the line for the outcome nobody wired.
    """
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


# --- the gate ---------------------------------------------------------------


@pytest.fixture
def every_blank_in_one_round(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let one round ask everything, for tests that are not about the cap.

    A round is capped so a person is not handed a wall of text, and the overflow
    carries to the next one. A test naming one particular blank should not also
    be asserting that it out-ranked five others.
    """
    monkeypatch.setattr(ranking, "CAP", 50)


async def test_a_drawing_that_is_not_a_process_never_reaches_review(
    connection: asyncpg.Connection,
):
    # The seed board stops at two steps without saying so and leaves an outcome
    # with no line out of it. Those are ten seconds of canvas work, and spending
    # a round on them would ask a person to answer what they can simply draw.
    unfixed = await boards.save(connection, board_from_file(SEED))

    with pytest.raises(IncompleteError) as refused:
        await run.review(connection, unfixed, transport=model_asking(proposed()))

    assert len(refused.value.findings) == 3
    assert await threads_repo.for_board(connection, unfixed) == ()


# --- a round ----------------------------------------------------------------


async def test_a_round_writes_what_it_asked(connection: asyncpg.Connection, board_id):
    result = await run.review(connection, board_id, transport=model_asking(proposed()))

    stored = await threads_repo.for_board(connection, board_id)
    assert [c.question for c in stored] == [c.question for c in result.asked]
    assert all(c.round == 1 for c in stored)


@pytest.mark.usefixtures("every_blank_in_one_round")
async def test_a_blank_is_asked_in_the_words_the_rule_wrote(
    connection: asyncpg.Connection, board_id
):
    # The model never sees the blanks, so nothing else would ask about them.
    result = await run.review(connection, board_id, transport=model_asking())

    asked = {c.question for c in result.asked}
    assert "Nobody is named as receiving this." in asked
    assert all(c.origin == "lint" for c in result.asked)


@pytest.mark.usefixtures("every_blank_in_one_round")
async def test_a_blank_is_recognised_by_where_it_is(connection: asyncpg.Connection, board_id):
    # Without an identity a blank is re-asked every round for as long as it stays
    # blank — which is forever, since nothing fills it but an answer.
    result = await run.review(connection, board_id, transport=model_asking())

    keys = {c.decision_key for c in result.asked}
    assert "lint:primitive:report_coa_discrepancy:recipients" in keys


async def test_no_more_than_six_questions_reach_anyone(connection: asyncpg.Connection, board_id):
    many = [proposed(question=f"question {n}", anchors=["primitive:coas_valid"]) for n in range(20)]
    result = await run.review(connection, board_id, transport=model_asking(*many))

    assert len(result.asked) <= ranking.CAP


async def test_the_situations_walked_are_kept_for_next_time(
    connection: asyncpg.Connection, board_id
):
    await run.review(connection, board_id, transport=model_asking(proposed()))
    stored = await scenarios_repo.for_board(connection, board_id)

    assert {s.key for s in stored} >= {"happy_path", "coas_valid_missing_coa"}
    assert all(s.dryrun_result for s in stored)


async def test_a_round_counts_itself(connection: asyncpg.Connection, board_id):
    await run.review(connection, board_id, transport=model_asking(proposed()))
    assert (await boards.get(connection, board_id)).review_round == 1

    await run.review(connection, board_id, transport=model_asking())
    assert (await boards.get(connection, board_id)).review_round == 2


# --- what a second round does -----------------------------------------------


async def test_a_question_already_asked_is_not_asked_again(
    connection: asyncpg.Connection, board_id
):
    first = await run.review(connection, board_id, transport=model_asking())
    again = await run.review(connection, board_id, transport=model_asking())

    assert first.asked
    assert {c.decision_key for c in again.asked}.isdisjoint({c.decision_key for c in first.asked})


async def test_a_question_that_was_dismissed_does_not_come_back(
    connection: asyncpg.Connection, board_id
):
    first = await run.review(connection, board_id, transport=model_asking())
    dismissed = next(c for c in first.asked if c.decision_key)
    assert dismissed.id is not None
    await threads_repo.set_status(connection, dismissed.id, "rejected")

    again = await run.review(connection, board_id, transport=model_asking())
    assert dismissed.decision_key not in {c.decision_key for c in again.asked}


# --- the revision loop ------------------------------------------------------


async def test_knowing_the_answer_does_not_resolve_anything(
    connection: asyncpg.Connection, board_id
):
    # `answered` means the knowledge exists. The drawing still has to show it,
    # and the owner does not get to declare that it does.
    first = await run.review(connection, board_id, transport=model_asking(proposed()))
    raised = next(c for c in first.asked if c.scenario_key)
    assert raised.id is not None
    await threads_repo.set_status(connection, raised.id, "answered")

    await run.review(connection, board_id, transport=model_asking())

    still = next(c for c in await threads_repo.for_board(connection, board_id) if c.id == raised.id)
    assert still.status == "answered"


async def test_redrawing_the_board_is_what_resolves_a_question(
    connection: asyncpg.Connection, board_id
):
    # The whole claim. The question was raised because a situation dead-ended;
    # once the owner draws the way back, that same walk reaches an ending, and
    # only then is it resolved.
    first = await run.review(connection, board_id, transport=model_asking(proposed()))
    raised = next(c for c in first.asked if c.scenario_key == "coas_valid_missing_coa")
    assert raised.id is not None
    await threads_repo.set_status(connection, raised.id, "answered")

    board = await boards.get(connection, board_id)
    with_a_way_back = board.model_copy(
        update={
            "edges": (
                *board.edges,
                Edge(
                    key="e7",
                    from_key="report_coa_discrepancy",
                    to_key="coas_valid",
                    relation="repeat",
                ),
            )
        }
    )
    await boards.save(connection, with_a_way_back)

    await run.review(connection, board_id, transport=model_asking())

    now = next(c for c in await threads_repo.for_board(connection, board_id) if c.id == raised.id)
    assert now.status == "resolved"
    assert now.resolved_at is not None


# --- what survives a settled conversation ------------------------------------


async def test_an_answered_question_becomes_a_statement(connection: asyncpg.Connection, board_id):
    # The reviewer asked well and then dropped every answer on the floor: nothing
    # called `distil`, so no statement could ever be written and the freeze could
    # only ever be handed an empty tuple. This is that hole.
    first = await run.review(connection, board_id, transport=model_asking(proposed()))
    answered = first.asked[0]
    assert answered.id is not None
    await threads_repo.add_message(connection, answered.id, "human", "It comes back once fixed.")
    await threads_repo.set_status(connection, answered.id, "answered")

    await run.review(connection, board_id, transport=model_asking(settling=[statement()]))

    stored = await assertions_repo.for_board(connection, board_id)
    assert [(str(a.anchor), a.kind) for a in stored] == [("primitive:coas_valid", "exception")]
    assert stored[0].thread_id == answered.id


async def test_an_answer_that_settles_nothing_leaves_the_question_open(
    connection: asyncpg.Connection, board_id
):
    # "Yeah, it depends" distils to nothing, and a question nobody actually
    # answered must not be marked as one they did.
    first = await run.review(connection, board_id, transport=model_asking(proposed()))
    vague = next(c for c in first.asked if c.evidence is None)
    assert vague.id is not None
    await threads_repo.set_status(connection, vague.id, "answered")

    await run.review(connection, board_id, transport=model_asking(settling=[]))

    still = next(c for c in await threads_repo.for_board(connection, board_id) if c.id == vague.id)
    assert still.status == "answered"
    assert await assertions_repo.for_board(connection, board_id) == ()


async def test_a_semantic_question_is_resolved_by_what_it_settled(
    connection: asyncpg.Connection, board_id
):
    # A question about what a value MEANS has no situation behind it, so
    # re-walking cannot prove it closed. What proves it is that the answer
    # produced a statement — otherwise the best half of the review can never
    # leave `answered`.
    first = await run.review(connection, board_id, transport=model_asking(proposed()))
    asked = next(c for c in first.asked if c.evidence is None)
    assert asked.id is not None
    await threads_repo.set_status(connection, asked.id, "answered")

    await run.review(connection, board_id, transport=model_asking(settling=[statement()]))

    now = next(c for c in await threads_repo.for_board(connection, board_id) if c.id == asked.id)
    assert now.status == "resolved"


async def test_a_dismissed_question_still_leaves_knowledge(
    connection: asyncpg.Connection, board_id
):
    # "Expiry is not checked at this stage" is knowledge. It stops a later round
    # re-asking and it tells a code generator the case was considered rather
    # than missed, so a dismissal has to distil like an answer does.
    first = await run.review(connection, board_id, transport=model_asking(proposed()))
    # One with no situation behind it, deliberately. A dismissal that cites a
    # walk is protected by the walk still reproducing; one that cites nothing has
    # only the rule that `rejected` is terminal standing between it and being
    # quietly marked resolved by the answer path.
    dismissed = next(c for c in first.asked if c.evidence is None)
    assert dismissed.id is not None
    await threads_repo.set_status(connection, dismissed.id, "rejected")

    await run.review(
        connection,
        board_id,
        transport=model_asking(settling=[statement(statement="Expiry is not checked here.")]),
    )

    (stored,) = await assertions_repo.for_board(connection, board_id)
    assert stored.kind == "negative"
    still = next(
        c for c in await threads_repo.for_board(connection, board_id) if c.id == dismissed.id
    )
    assert still.status == "rejected"


async def test_a_conversation_is_distilled_once(connection: asyncpg.Connection, board_id):
    # Every round would otherwise re-distil every settled thread, and the same
    # sentence would arrive again on every card that inherits it.
    first = await run.review(connection, board_id, transport=model_asking(proposed()))
    answered = first.asked[0]
    assert answered.id is not None
    await threads_repo.set_status(connection, answered.id, "answered")

    await run.review(connection, board_id, transport=model_asking(settling=[statement()]))
    await run.review(connection, board_id, transport=model_asking(settling=[statement()]))

    assert len(await assertions_repo.for_board(connection, board_id)) == 1


# --- pressing on an answer ---------------------------------------------------


async def test_pressing_on_an_answer_is_a_turn_in_the_same_conversation(
    connection: asyncpg.Connection, board_id
):
    first = await run.review(connection, board_id, transport=model_asking(proposed()))
    hedged = first.asked[0]
    assert hedged.id is not None
    await threads_repo.add_message(connection, hedged.id, "human", "Usually the supervisor.")
    await threads_repo.set_status(connection, hedged.id, "answered")

    again = await run.review(
        connection,
        board_id,
        transport=model_asking(
            proposed(question="What counts as urgent?", follows_up=str(hedged.id))
        ),
    )

    threads = await threads_repo.for_board(connection, board_id)
    pressed = next(c for c in threads if c.id == hedged.id)
    assert [m.body for m in pressed.messages][-1] == "What counts as urgent?"
    # The follow-up is a turn, so it is nowhere among the questions — including
    # the ones this round newly asked, which carry blanks that overflowed the cap.
    assert "What counts as urgent?" not in {c.question for c in threads}
    assert "What counts as urgent?" not in {c.question for c in again.asked}


async def test_pressing_on_an_answer_re_opens_it(connection: asyncpg.Connection, board_id):
    # `answered` means the knowledge exists. Once the reviewer says the answer
    # left something undecided, it does not.
    first = await run.review(connection, board_id, transport=model_asking(proposed()))
    hedged = first.asked[0]
    assert hedged.id is not None
    await threads_repo.set_status(connection, hedged.id, "answered")

    again = await run.review(
        connection, board_id, transport=model_asking(proposed(follows_up=str(hedged.id)))
    )

    now = next(c for c in await threads_repo.for_board(connection, board_id) if c.id == hedged.id)
    assert now.status == "open"
    assert again.reopened == (hedged.id,)


async def test_six_things_at_most_counting_the_follow_ups(connection: asyncpg.Connection, board_id):
    first = await run.review(connection, board_id, transport=model_asking(proposed()))
    hedged = first.asked[0]
    assert hedged.id is not None
    await threads_repo.set_status(connection, hedged.id, "answered")

    again = await run.review(
        connection,
        board_id,
        transport=model_asking(
            proposed(question="What counts as urgent?", follows_up=str(hedged.id)),
            *[proposed(question=f"question {n}") for n in range(20)],
        ),
    )

    assert len(again.asked) + len(again.reopened) <= 6


async def test_pressing_on_a_question_still_open_re_opens_nothing(
    connection: asyncpg.Connection, board_id
):
    # It gets the turn, because a question can always be sharpened. But nothing
    # changed status, and reporting it as reopened tells someone a conversation
    # went backwards when it did not.
    first = await run.review(connection, board_id, transport=model_asking(proposed()))
    still_open = first.asked[0]
    assert still_open.id is not None
    assert still_open.status == "open"

    again = await run.review(
        connection, board_id, transport=model_asking(proposed(follows_up=str(still_open.id)))
    )

    pressed = next(
        c for c in await threads_repo.for_board(connection, board_id) if c.id == still_open.id
    )
    assert len(pressed.messages) == 1
    assert again.reopened == ()


async def test_a_round_cannot_be_all_follow_ups(connection: asyncpg.Connection, board_id):
    # Two rounds first, so there are more conversations open than a round may
    # spend — the blanks that overflowed round one arrive in round two. Pressing
    # on all of them uncapped is how a review becomes a wall of text, and it also
    # drives the remaining budget negative, which silently *widens* the cap on
    # new questions rather than closing it.
    await run.review(connection, board_id, transport=model_asking(proposed()))
    await run.review(connection, board_id, transport=model_asking())
    open_now = await threads_repo.for_board(connection, board_id)
    assert len(open_now) > ranking.CAP  # round one overflowed into round two

    for comment in open_now:
        assert comment.id is not None
        await threads_repo.set_status(connection, comment.id, "answered")

    again = await run.review(
        connection,
        board_id,
        transport=model_asking(*[proposed(follows_up=str(c.id)) for c in open_now]),
    )

    assert len(again.reopened) + len(again.asked) <= ranking.CAP


async def test_a_question_answered_before_the_canvas_was_edited_still_resolves(
    connection: asyncpg.Connection, board_id
):
    # People answer, then redraw later — often much later. Distilling once and
    # deciding resolution once are different questions, and settling them
    # together strands every conversation whose answer arrived before the edit.
    first = await run.review(connection, board_id, transport=model_asking(proposed()))
    raised = next(c for c in first.asked if c.scenario_key == "coas_valid_missing_coa")
    assert raised.id is not None
    await threads_repo.set_status(connection, raised.id, "answered")

    # A round where they answered but changed nothing: it distils, and stays put.
    await run.review(connection, board_id, transport=model_asking(settling=[statement()]))
    waiting = next(
        c for c in await threads_repo.for_board(connection, board_id) if c.id == raised.id
    )
    assert waiting.status == "answered"

    board = await boards.get(connection, board_id)
    await boards.save(
        connection,
        board.model_copy(
            update={
                "edges": (
                    *board.edges,
                    Edge(
                        key="e7",
                        from_key="report_coa_discrepancy",
                        to_key="coas_valid",
                        relation="repeat",
                    ),
                )
            }
        ),
    )

    await run.review(connection, board_id, transport=model_asking())

    now = next(c for c in await threads_repo.for_board(connection, board_id) if c.id == raised.id)
    assert now.status == "resolved"
    # And it was not distilled a second time on the way.
    assert len(await assertions_repo.for_board(connection, board_id)) == 1


async def test_a_question_about_meaning_is_not_held_hostage_by_a_walk_it_cited(
    connection: asyncpg.Connection, board_id
):
    # The model cites a walk far more often than a walk is the point. It attached
    # one to "do all the line items get checked before anything is reported",
    # which no redraw will ever change — so judged on evidence alone that
    # conversation could never close, and a question nobody can settle blocks the
    # freeze for good. What raised it was silence, and a statement ends silence.
    first = await run.review(
        connection,
        board_id,
        transport=model_asking(
            proposed(
                category="ambiguous_rule",
                question="What counts as the same batch number?",
                scenario_key="coas_valid_missing_coa",
            )
        ),
    )
    cited = next(c for c in first.asked if c.category == "ambiguous_rule")
    assert cited.evidence is not None
    assert cited.id is not None
    await threads_repo.set_status(connection, cited.id, "answered")

    # Nothing is redrawn, so the walk reproduces exactly as before.
    await run.review(connection, board_id, transport=model_asking(settling=[statement()]))

    now = next(c for c in await threads_repo.for_board(connection, board_id) if c.id == cited.id)
    assert now.status == "resolved"


async def test_a_citation_that_is_not_a_walk_does_not_decide_resolution(
    connection: asyncpg.Connection, board_id
):
    # `Evidence` names the tool that produced it, and only a walk is something a
    # redraw can change. A question citing the list of places two values have to
    # match is closed by somebody saying what a match is — re-running that list
    # would return the same joins forever, and the conversation would never end.
    await threads_repo.save(
        connection,
        board_id,
        Thread(
            category="undefined_exception",
            status="answered",
            origin="semantic",
            question="How do you know two of these are the same thing?",
            anchors=(Anchor.parse("primitive:coas_valid"),),
            evidence=Evidence(
                tool="where_values_meet",
                result="1 join with nothing said about it",
                detail={
                    "sides": ["commercial_invoice.batch_no", "certificate_of_analysis.batch_no"]
                },
            ),
        ),
    )

    await run.settle(connection, board_id, transport=model_asking(settling=[statement()]))

    (settled,) = await threads_repo.for_board(connection, board_id)
    assert settled.status == "resolved"
