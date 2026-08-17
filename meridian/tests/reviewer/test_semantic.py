"""Tests for the one model call in the review.

Every one runs offline. The model is the only part of this system allowed to be
creative, so the tests are almost all about what happens to what it returns:
an anchor that points at nothing, an identity it invented, a citation to a walk
that never happened. A question stored pointing at a card that does not exist is
a pin on an empty canvas, and nobody can act on it.

What the model is trusted with is wording. What it is not trusted with is
asserting that the board does something.
"""

import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from meridian.domain.graph import Board
from meridian.domain.review import Anchor, Assertion, CommentMessage, Thread
from meridian.reviewer import dryrun, scenarios, semantic


@dataclass
class Turn:
    output: list[Any]
    output_parsed: Any = None


class FakeTransport:
    """Serves one canned answer and records what it was sent."""

    def __init__(self, *questions: dict[str, Any]) -> None:
        self.answer = semantic.Questions.model_validate({"questions": list(questions)})
        self.sent: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> Any:
        self.sent.append(kwargs)
        return Turn(output=[], output_parsed=self.answer)


@dataclass
class Called:
    """One tool call, in the shape `structured()` reads off a response."""

    name: str
    arguments: str
    call_id: str = "call_1"
    type: str = "function_call"


class ToolCallingTransport(FakeTransport):
    """Asks for a tool on the first turn, then answers."""

    async def __call__(self, **kwargs: Any) -> Any:
        self.sent.append(kwargs)
        if len(self.sent) == 1:
            return Turn(output=[Called(name="where_values_meet", arguments="{}")])
        return Turn(output=[], output_parsed=self.answer)


def proposed(**overrides: Any) -> dict[str, Any]:
    return {
        "category": "undefined_exception",
        "severity": "important",
        "question": "What happens after the discrepancy is reported?",
        "reason": "The drawing stops there and nothing says the shipment comes back.",
        "anchors": ["primitive:coas_valid"],
        "decision_key": None,
        "scenario_key": None,
    } | overrides


async def ask(board: Board, *questions: dict[str, Any]):
    walked = [(s, dryrun.walk(board, s)) for s in scenarios.enumerate_from(board)]
    transport = FakeTransport(*questions)
    asked = await semantic.ask(board, walked, transport=transport)
    return asked.comments, transport


# --- what the model is trusted with -----------------------------------------


async def test_a_question_becomes_something_that_can_be_pinned_to_a_card(seed: Board):
    (comment,), _ = await ask(seed, proposed())

    assert comment.question.startswith("What happens")
    assert comment.reason
    assert [str(a) for a in comment.anchors] == ["primitive:coas_valid"]
    assert comment.origin == "semantic"


async def test_the_model_sees_the_board_and_where_every_situation_ended_up(seed: Board):
    _, transport = await ask(seed, proposed())
    sent = json.dumps(transport.sent[0]["input"])

    assert "coas_valid" in sent
    assert "mismatched_coa" in sent
    # The walk results, not just the situations: an undefined branch is the
    # single most useful thing on this board and it only exists once walked.
    assert "undefined_branch" in sent


async def test_the_model_is_given_something_to_do_besides_read(seed: Board):
    # Both tools exist so a claim can be checked rather than asserted: one walks
    # a situation, the other collects every point two values have to match.
    _, transport = await ask(seed, proposed())
    assert [t["name"] for t in transport.sent[0]["tools"]] == ["dry_run", "where_values_meet"]


# --- what the model is not trusted with -------------------------------------


async def test_a_question_pinned_to_a_card_that_does_not_exist_is_dropped(seed: Board):
    asked, _ = await ask(seed, proposed(anchors=["primitive:invented_card"]))
    assert asked == ()


async def test_a_question_keeps_the_anchors_that_resolve_and_loses_the_rest(seed: Board):
    (comment,), _ = await ask(
        seed, proposed(anchors=["primitive:invented_card", "primitive:coas_valid", "edge:e5"])
    )
    assert [str(a) for a in comment.anchors] == ["primitive:coas_valid", "edge:e5"]


async def test_an_identity_the_model_invented_is_not_kept(seed: Board):
    # `decision_key` is how a question is recognised as one already asked. If the
    # model could mint one, it could silence a real question next round by
    # colliding with it, or make its own unaskable-again by inventing a key
    # nothing else produces.
    (comment,), _ = await ask(seed, proposed(decision_key="sequence:something_i_made_up"))
    assert comment.decision_key is None


async def test_an_identity_the_board_really_produced_is_kept(seed: Board):
    real = "termination:edge:e5|primitive:report_coa_discrepancy"
    (comment,), _ = await ask(seed, proposed(decision_key=real))
    assert comment.decision_key == real


async def test_a_question_citing_a_walk_carries_that_walk(seed: Board):
    (comment,), _ = await ask(seed, proposed(scenario_key="coas_valid_mismatched_coa"))

    assert comment.scenario_key == "coas_valid_mismatched_coa"
    assert comment.evidence is not None
    assert comment.evidence.result == "undefined_branch"


async def test_a_question_citing_a_walk_that_never_happened_cites_nothing(seed: Board):
    # Not dropped — the question may still be good. But it may not claim a walk
    # backs it, because validation re-runs evidence and this would not reproduce.
    (comment,), _ = await ask(seed, proposed(scenario_key="a_walk_nobody_took"))

    assert comment.scenario_key is None
    assert comment.evidence is None


async def test_a_round_where_the_model_finds_nothing_is_allowed(seed: Board):
    asked, _ = await ask(seed)
    assert asked == ()


async def test_the_round_is_carried_onto_every_question(seed: Board):
    walked = [(s, dryrun.walk(seed, s)) for s in scenarios.enumerate_from(seed)]
    asked = await semantic.ask(seed, walked, round=3, transport=FakeTransport(proposed()))
    assert [c.round for c in asked.comments] == [3]


async def test_a_question_nobody_could_point_at_is_dropped(seed: Board):
    # `board` is the one anchor that is not a place. A comment carrying only that
    # cannot be a pin on a card, and the product's whole claim is comments on the
    # canvas. Measured breaking: asked twice, one run put every question on
    # `board` and none of them could have been shown to anyone.
    asked, _ = await ask(seed, proposed(anchors=["board"]))
    assert asked == ()


async def test_the_whole_process_can_still_be_named_alongside_a_card(seed: Board):
    (comment,), _ = await ask(seed, proposed(anchors=["primitive:coas_valid", "board"]))
    assert [str(a) for a in comment.anchors] == ["primitive:coas_valid", "board"]


async def test_the_pin_goes_on_the_first_thing_that_can_be_drawn(seed: Board):
    # The interface pins to the first drawable anchor and highlights the rest on
    # hover, so a question ordered `board` first would land its pin nowhere.
    (comment,), _ = await ask(seed, proposed(anchors=["board", "edge:e5", "primitive:coas_valid"]))
    assert str(comment.primary_anchor()) == "edge:e5"


async def test_a_round_reports_what_it_threw_away(seed: Board):
    # Three of four problems found in a day were invisible until someone ran it
    # by hand. A number would have shown all of them.
    walked = [(s, dryrun.walk(seed, s)) for s in scenarios.enumerate_from(seed)]
    transport = FakeTransport(
        proposed(anchors=["primitive:coas_valid"]),
        proposed(anchors=["primitive:invented"]),
        proposed(anchors=["board"]),
    )
    asked = await semantic.ask(seed, walked, transport=transport)

    assert len(asked.comments) == 1
    assert asked.dropped == {"anchor did not resolve": 1, "nothing to pin it to": 1}


# --- pressing on an answer that settled nothing ------------------------------


ANSWERED = Thread(
    id=UUID("11111111-1111-1111-1111-111111111111"),
    category="missing_context",
    status="answered",
    question="Who receives the discrepancy report?",
    anchors=(Anchor.parse("primitive:report_coa_discrepancy"),),
    messages=(
        CommentMessage(seq=1, author="ai", body="Who receives the discrepancy report?"),
        CommentMessage(seq=2, author="human", body="Usually the supervisor, unless it's urgent."),
    ),
)


async def followed_up(board: Board, prior: tuple[Thread, ...], **overrides: Any):
    walked = [(s, dryrun.walk(board, s)) for s in scenarios.enumerate_from(board)]
    return await semantic.ask(
        board, walked, threads=prior, transport=FakeTransport(proposed(**overrides))
    )


async def test_pressing_on_an_answer_is_a_turn_rather_than_a_new_question(seed: Board):
    # "Usually the supervisor, unless it's urgent" leaves something undecided,
    # and asking what counts as urgent belongs in the conversation that raised
    # it — not as a seventh question with no history attached.
    asked = await followed_up(
        seed,
        (ANSWERED,),
        follows_up=str(ANSWERED.id),
        question="What counts as urgent?",
    )

    assert asked.comments == ()
    assert asked.follow_ups == ((ANSWERED.id, "What counts as urgent?"),)


async def test_pressing_on_a_dismissed_question_is_refused(seed: Board):
    # `rejected` means considered and set aside. Re-opening it is the fastest
    # way to lose someone's attention, and it is the one thing dedup exists for.
    asked = await followed_up(
        seed, (ANSWERED.model_copy(update={"status": "rejected"}),), follows_up=str(ANSWERED.id)
    )

    assert asked.follow_ups == ()
    assert asked.dropped == {"nothing to follow up on": 1}


async def test_pressing_on_a_question_nobody_asked_is_refused(seed: Board):
    asked = await followed_up(seed, (ANSWERED,), follows_up="99999999-9999-9999-9999-999999999999")

    assert asked.follow_ups == ()
    assert asked.dropped == {"nothing to follow up on": 1}


# --- what has already been decided -------------------------------------------


async def test_what_is_already_settled_is_shown_to_the_model(seed: Board):
    # Without this the model re-asks what round one answered, because prior
    # threads show what was ASKED and the answer lives in a turn it has to
    # reconstruct. A settled statement says what was decided, in one line.
    walked = [(s, dryrun.walk(seed, s)) for s in scenarios.enumerate_from(seed)]
    transport = FakeTransport()
    await semantic.ask(
        seed,
        walked,
        settled=(
            Assertion(
                anchor=Anchor.parse("primitive:coas_valid"),
                kind="exception",
                statement="A corrected certificate re-runs every batch.",
            ),
        ),
        transport=transport,
    )

    sent = json.dumps(transport.sent[0]["input"])
    assert "A corrected certificate re-runs every batch." in sent


async def test_a_statement_that_has_been_replaced_is_not_shown(seed: Board):
    # A superseded statement is kept for the audit trail and is no longer true.
    # Shown to the reviewer it would argue against the answer that replaced it.
    walked = [(s, dryrun.walk(seed, s)) for s in scenarios.enumerate_from(seed)]
    transport = FakeTransport()
    await semantic.ask(
        seed,
        walked,
        settled=(
            Assertion(
                anchor=Anchor.parse("primitive:coas_valid"),
                kind="exception",
                statement="An outdated statement nobody stands behind.",
                superseded_by=UUID("22222222-2222-2222-2222-222222222222"),
            ),
        ),
        transport=transport,
    )

    sent = json.dumps(transport.sent[0]["input"])
    assert "An outdated statement" not in sent


async def test_the_model_may_not_call_its_own_question_blocking(seed: Board):
    # `blocking` is the strongest word in the system and it means one thing: the
    # drawing does not work as a process. Provable, visible on the canvas, and
    # closable by anyone. A question about what a value means is never that, and
    # on the first live run the model reached for the word anyway.
    (comment,), _ = await ask(seed, proposed(severity="blocking"))
    assert comment.severity == "important"


async def test_a_severity_the_model_is_allowed_to_choose_is_kept(seed: Board):
    (comment,), _ = await ask(seed, proposed(severity="minor"))
    assert comment.severity == "minor"


async def test_a_round_reports_which_tools_it_actually_used(seed: Board):
    # A tool exists to make a claim checkable, so a tool nobody calls is a claim
    # nobody checked — and from the questions alone those look identical.
    walked = [(s, dryrun.walk(seed, s)) for s in scenarios.enumerate_from(seed)]
    transport = ToolCallingTransport(proposed())

    asked = await semantic.ask(seed, walked, transport=transport)

    assert asked.consulted == {"where_values_meet": 1}


# --- a claim about a walk needs a walk --------------------------------------


def test_a_reason_may_not_describe_a_walk_it_did_not_take() -> None:
    """The model writes the situation, the outcomes and the narration.

    So "I walked a case where one line matched and one had no COA" reads as the
    strongest evidence in a thread and is prose. When no scenario is attached
    there is nothing to check it against, and a citation nobody can follow is
    worse than none — the question stays, the claim goes.
    """
    assert semantic._claims_a_walk("I walked a case with one matching line and it passed.")
    assert semantic._claims_a_walk("I traced the mismatch and it ended at the report.")
    assert semantic._claims_a_walk("I ran the case where the COA never arrives.")


def test_a_claim_about_the_board_is_not_a_claim_about_a_walk() -> None:
    """Narrow on purpose, or every reason mentioning a path loses its reason."""
    assert not semantic._claims_a_walk("Nothing says what happens when this is walked.")
    assert not semantic._claims_a_walk("The walk from the check has no path for a mixed result.")
    assert not semantic._claims_a_walk("The board does not say whether one bad line fails it.")
    assert not semantic._claims_a_walk(None)


def test_the_question_survives_when_the_claim_is_stripped() -> None:
    """Only the unsupported sentence goes. The question is usually a good one —
    what is unsupported is the claim it was *seen* rather than reasoned."""
    board = Board.model_validate(
        {
            "name": "t",
            "primitives": [
                {"key": "it_arrives", "primitive_type": "event", "config": {"name": "It arrives"}},
                {
                    "key": "all_done",
                    "primitive_type": "action",
                    "config": {"name": "Done", "effect": "noop", "is_terminal": True},
                },
            ],
            "edges": [
                {
                    "key": "e1",
                    "from_key": "it_arrives",
                    "to_key": "all_done",
                    "relation": "normal",
                    "on_outcomes": [],
                }
            ],
            "layout": {},
        }
    )
    proposed = semantic.Proposed(
        category="spec_gap",
        severity="important",
        question="If only some lines match, what happens to the shipment?",
        reason="I walked a case with one matching line and it went to the all-clear path.",
        anchors=["primitive:it_arrives"],
        scenario_key="a_probe_that_does_not_exist",
    )

    comment, why = semantic._checked(board, proposed, set(), {}, 1)

    assert comment is not None, why
    assert comment.question == proposed.question
    assert comment.reason is None
    assert comment.scenario_key is None
