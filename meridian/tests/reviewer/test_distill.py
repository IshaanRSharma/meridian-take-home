"""Tests for turning a settled conversation into statements that outlive it.

Every one runs offline. `structured()` calls a function and the default happens
to talk to OpenAI, so a test hands it a fake and the whole module becomes
testable with no key — the same seam `tests/core/test_llm.py` establishes.

Two properties carry this file. A conversation about three things becomes three
statements about one thing each, because that is what makes a statement
compilable. And a statement pointing at a card the board does not have is
dropped here, loudly, rather than vanishing silently at the freeze.
"""

from dataclasses import dataclass
from typing import Any, get_args
from uuid import UUID

import pytest

from meridian.core.config import settings
from meridian.domain.graph import Board
from meridian.domain.review import Anchor, Assertion, CommentMessage, Thread
from meridian.reviewer.distill import (
    Distillation,
    Judged,
    Judgement,
    Statement,
    distil,
    paraphrases,
)

# ── the fake transport ───────────────────────────────────────────────────
# One canned turn, no tool calls: distillation asks the model for an object and
# nothing else. The stub carries the two attributes `structured()` reads off a
# response, so a loop that started reading a third would fail here rather than
# quietly find `None`.


@dataclass
class StubResponse:
    output: list[Any]
    output_parsed: Any = None


class FakeTransport:
    """Serves one canned distillation and records what it was sent."""

    def __init__(self, *statements: Statement) -> None:
        self.turn = StubResponse(output=[], output_parsed=Distillation(statements=list(statements)))
        self.sent: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> Any:
        self.sent.append(kwargs)
        return self.turn


@pytest.fixture(autouse=True)
def model_is_configured(monkeypatch: pytest.MonkeyPatch):
    """A model name, so these pass on a machine that has never seen a key."""
    monkeypatch.setenv("OPENAI_MODEL", "configured-default")
    settings.cache_clear()
    yield
    settings.cache_clear()


def says(anchor: str, kind: str = "rule", statement: str = "It is done this way.", **extra):
    return Statement(anchor=anchor, kind=kind, statement=statement, **extra)


def conversation(**overrides) -> Thread:
    """The COA conversation from Claude.md §32.4, settled."""
    base = {
        "category": "undefined_exception",
        "status": "answered",
        "question": "A certificate problem ends the process — is that right?",
        "anchors": (
            Anchor(kind="primitive", key="coas_valid"),
            Anchor(kind="edge", key="e5"),
            Anchor(kind="primitive", key="report_coa_discrepancy"),
        ),
        "messages": (
            CommentMessage(seq=1, author="ai", body="Does a certificate problem end this?"),
            CommentMessage(
                seq=2,
                author="human",
                body="No — the supplier sends a corrected certificate. We wait 48 hours, "
                "then it goes to the receiving supervisor.",
            ),
        ),
    }
    return Thread(**(base | overrides))


async def distilled(board: Board, thread: Thread, *statements: Statement):
    return await distil(board, thread, transport=FakeTransport(*statements))


# --- one conversation, several statements -----------------------------------


async def test_a_conversation_about_three_things_becomes_a_statement_about_each(seed: Board):
    # The whole design in one assertion: a Thread has many anchors and an
    # Assertion has one, so the answer is split across the elements it settles
    # rather than repeated onto all of them. Three different anchor kinds, so an
    # implementation that reused the thread's primary anchor for everything
    # produces three identical anchors and fails.
    settled = await distilled(
        seed,
        conversation(),
        says("edge:e5", "timing", "Wait 48 hours for a corrected certificate."),
        says("primitive:coas_valid", "exception", "Re-run every batch when a correction lands."),
        says("entity_field:certificate_of_analysis.batch_no", "terminology", "Batch, not page."),
    )

    assert [(str(a.anchor), a.kind, a.statement) for a in settled] == [
        ("edge:e5", "timing", "Wait 48 hours for a corrected certificate."),
        ("primitive:coas_valid", "exception", "Re-run every batch when a correction lands."),
        ("entity_field:certificate_of_analysis.batch_no", "terminology", "Batch, not page."),
    ]


async def test_a_conversation_that_settled_nothing_produces_nothing(seed: Board):
    assert await distilled(seed, conversation()) == ()


# --- an anchor has to resolve ------------------------------------------------


@pytest.mark.parametrize(
    "ref",
    [
        "board",
        "primitive:coas_valid",
        "edge:e5",
        "entity_field:commercial_invoice.invoice_no",
        "entity_field:commercial_invoice.line_items[].hts_number",
    ],
)
async def test_a_statement_about_a_real_element_is_kept(seed: Board, ref: str):
    settled = await distilled(seed, conversation(), says(ref))
    assert [str(a.anchor) for a in settled] == [ref]


@pytest.mark.parametrize(
    "ref",
    [
        "primitive:escalate_supervisor",  # the card the answer implies, not yet drawn
        "edge:e_recheck",
        "entity_field:commercial_invoice.batch_nos",  # a plausible field this entity lacks
        "entity_field:no_such_entity.invoice_no",
        "group:reporting",  # no card carries this region
    ],
)
async def test_a_statement_about_something_the_board_does_not_have_is_dropped(
    seed: Board, ref: str
):
    # A statement pointing at a card that does not exist would be written, then
    # reach nothing at the freeze, and the loss would be invisible. The second
    # statement is the real one on purpose: an implementation that stopped at
    # the first failure, or took only the head of the list, returns nothing.
    settled = await distilled(seed, conversation(), says(ref), says("primitive:coas_valid"))
    assert [str(a.anchor) for a in settled] == ["primitive:coas_valid"]


@pytest.mark.parametrize("ref", ["edge:coas_valid", "primitive:e5", "entity_field:coas_valid.key"])
async def test_an_anchor_is_checked_against_the_right_kind_of_element(seed: Board, ref: str):
    # Every key here exists on the board under a different kind. Resolution that
    # only asked "have I seen this string" would keep all three, and a statement
    # about a transition would end up inside a file that only knows a step.
    assert await distilled(seed, conversation(), says(ref)) == ()


@pytest.mark.parametrize("ref", ["", "nonsense:whatever", "primitive", "board:the_whole_thing"])
async def test_a_reference_that_is_not_even_an_anchor_is_dropped(seed: Board, ref: str):
    # A malformed reference is the model's mistake, not the round's: it must not
    # take the statements beside it down with it.
    settled = await distilled(seed, conversation(), says(ref), says("board"))
    assert [str(a.anchor) for a in settled] == ["board"]


async def test_a_statement_about_a_region_is_kept_once_a_card_belongs_to_it(seed: Board):
    # Groups are the one anchor whose members can change while the anchor
    # itself persists, and the scope chain reads them, so they have to resolve
    # by membership rather than be refused outright.
    grouped = seed.model_copy(
        update={
            "primitives": tuple(
                p.model_copy(update={"group_key": "reporting"}) if p.key == "coas_valid" else p
                for p in seed.primitives
            )
        }
    )

    settled = await distilled(grouped, conversation(), says("group:reporting"))

    assert [str(a.anchor) for a in settled] == ["group:reporting"]


# --- what every statement carries -------------------------------------------


async def test_every_statement_carries_the_conversation_it_came_from(seed: Board):
    # Provenance is the audit path — click a statement in the spec, land on the
    # conversation that produced it. Round 3, not the default, so an
    # implementation that never set it fails rather than agreeing by accident.
    thread = conversation(id=UUID("00000000-0000-4000-8000-0000000000aa"), round=3)

    settled = await distilled(seed, thread, says("board"), says("primitive:coas_valid"))

    assert [(a.thread_id, a.round) for a in settled] == [(thread.id, 3)] * 2


async def test_a_dismissed_concern_still_crosses_the_freeze_as_negative_knowledge(seed: Board):
    # `rejected` is not a delete. The dismissal is knowledge — it stops a later
    # round re-asking, and it tells codegen the case was considered. The model
    # proposes a rule here, which is the plausible wrong answer: status is a
    # fact about the conversation, so the kind is not the model's to choose.
    settled = await distilled(
        seed,
        conversation(status="rejected"),
        says("primitive:coas_valid", "rule", "Expiry is not checked at pre-alert."),
    )

    assert [(a.kind, a.statement) for a in settled] == [
        ("negative", "Expiry is not checked at pre-alert.")
    ]


# --- a constraint has to be checkable ----------------------------------------


async def test_a_constraint_arrives_as_json_and_lands_as_an_object(seed: Board):
    settled = await distilled(
        seed,
        conversation(),
        says(
            "entity_field:certificate_of_analysis.batch_no",
            "constraint",
            "A batch number is seven digits.",
            constraint=r'{"pattern": "^\\d{7}$"}',
        ),
    )

    assert [a.constraint_json for a in settled] == [{"pattern": r"^\d{7}$"}]


@pytest.mark.parametrize("literal", ["see the SOP", "[1, 2]"])
async def test_a_statement_that_is_not_a_constraint_survives_a_stray_constraint_field(
    seed: Board, literal: str
):
    # Only `constraint` promises to be checkable. Anything the model attaches to
    # a rule that is not an object is not a constraint by another name — it is
    # noise, and it must not take a settled rule down with it.
    settled = await distilled(
        seed,
        conversation(),
        says("primitive:coas_valid", "rule", "Batches match.", constraint=literal),
    )

    assert [(a.kind, a.constraint_json) for a in settled] == [("rule", None)]


@pytest.mark.parametrize("literal", [None, "seven digits", '"^d{7}$"', "[1, 2]"])
async def test_a_constraint_nobody_can_check_is_recorded_as_the_rule_it_is(
    seed: Board, literal: str | None
):
    # Prose, or JSON that is not an object, cannot be validated against a real
    # extracted value — which is the only reason `constraint` exists as a kind
    # separate from `rule`. So the label goes and the statement stays: it is
    # still true, still what a code generator reads, and now honest about not
    # being machine-checkable. Nothing counts it as checked, because nothing
    # counts a `rule` as checked.
    #
    # This refused outright until the first live run, where the model labelled a
    # matching rule a constraint and passed no object. One bad label raised, and
    # took five good statements from the same round with it.
    settled = await distilled(
        seed,
        conversation(),
        says("primitive:coas_valid", "constraint", "Seven digits.", constraint=literal),
    )

    assert [(a.kind, a.constraint_json) for a in settled] == [("rule", None)]


def test_the_wire_schema_offers_exactly_the_kinds_an_assertion_accepts():
    # Two Literals, one meaning. A kind the model can propose and an Assertion
    # cannot hold would fail at construction, in the round, in front of someone.
    assert get_args(Statement.model_fields["kind"].annotation) == get_args(
        Assertion.model_fields["kind"].annotation
    )


# --- what the model is shown -------------------------------------------------


async def test_the_model_is_shown_the_question_and_every_turn(seed: Board):
    # It is distilling an answer, so the answer has to be in front of it. The
    # question comes too: "48 hours" means nothing without what was asked.
    transport = FakeTransport()

    await distil(seed, conversation(), transport=transport)

    payload = transport.sent[0]["input"][0]["content"]
    assert "A certificate problem ends the process" in payload
    assert "then it goes to the receiving supervisor" in payload


async def test_the_model_is_shown_the_elements_it_may_anchor_to(seed: Board):
    # Anchors are validated after the fact regardless, but a model that has to
    # guess a key spends the round proposing statements that get dropped.
    transport = FakeTransport()

    await distil(seed, conversation(), transport=transport)

    payload = transport.sent[0]["input"][0]["content"]
    for ref in ("primitive:coas_valid", "edge:e5", "entity_field:commercial_invoice.invoice_no"):
        assert ref in payload


async def test_a_constraint_a_machine_can_apply_keeps_its_label(seed: Board):
    # The other half of the rule above: demoting must be about what the
    # statement carries, not about the word `constraint` being unreachable.
    settled = await distilled(
        seed,
        conversation(),
        says(
            "entity_field:commercial_invoice.invoice_no",
            "constraint",
            "An invoice number is seven digits.",
            constraint='{"pattern": "^\\\\d{7}$"}',
        ),
    )

    assert settled[0].kind == "constraint"
    assert settled[0].constraint_json == {"pattern": "^\\d{7}$"}


# --- did the answer say anything the drawing had not -------------------------


def judging(*verdicts: tuple[int, bool]):
    """A fake judge returning a fixed verdict per statement index."""
    judged = Judged(
        judgements=[
            Judgement(index=i, restates_the_drawing=restates, why="because")
            for i, restates in verdicts
        ]
    )

    async def transport(**_: Any) -> Any:
        return StubResponse(output=[], output_parsed=judged)

    return transport


def said(text: str) -> Assertion:
    return Assertion(anchor=Anchor(kind="primitive", key="coas_valid"), kind="rule", statement=text)


async def test_a_statement_that_only_restates_the_drawing_is_reported(seed: Board):
    # The measurement that separates a spec which encodes the answers from one
    # that merely contains them. This exact sentence came out of a live round:
    # correctly anchored, correctly inlined, correctly checksummed, and worth
    # nothing, because the edges already say it.
    settled = (
        said("The process reports a COA discrepancy when the COA check fails."),
        said("A corrected certificate re-runs every batch on the invoice."),
    )

    found = await paraphrases(seed, settled, transport=judging((0, True), (1, False)))

    assert [a.statement for a in found] == [settled[0].statement]


async def test_nothing_settled_is_nothing_to_judge(seed: Board):
    # And no call made: a round that settled nothing must not spend a request
    # asking a model to consider an empty list.
    async def never_called(**_: Any) -> Any:
        raise AssertionError("no statements, no call")

    assert await paraphrases(seed, (), transport=never_called) == ()


async def test_a_verdict_about_a_statement_that_was_not_given_is_ignored(seed: Board):
    # The judgement carries an index rather than the text, so a model that
    # invents one would otherwise silently condemn whichever statement happens
    # to sit at that position — or crash the round on a lookup.
    settled = (said("A corrected certificate re-runs every batch."),)

    found = await paraphrases(seed, settled, transport=judging((7, True), (-1, True)))

    assert found == ()


# --- what the element already knows -----------------------------------------


def _shown(fake: FakeTransport) -> str:
    """The payload the distiller was actually sent."""
    return str(fake.sent[0]["input"][0]["content"])


async def test_the_distiller_is_shown_what_these_elements_already_know(seed: Board):
    # Two conversations can settle the same fact onto one card, and then
    # `context_for` inlines the sentence twice into that card's spec entry. The
    # distiller cannot avoid it without being told what is already recorded.
    fake = FakeTransport()
    already = (
        Assertion(
            anchor=Anchor.parse("primitive:coas_valid"),
            kind="terminology",
            statement="A batch matches after trimming spaces.",
        ),
    )
    thread = Thread(
        category="ambiguous_rule",
        question="How do you compare them?",
        anchors=(Anchor.parse("primitive:coas_valid"),),
    )

    await distil(seed, thread, already, transport=fake)

    assert "trimming spaces" in _shown(fake)


async def test_a_statement_about_an_unrelated_card_is_not_shown(seed: Board):
    # The safety property. Two subgraphs can hold near-identical rules and BOTH
    # are needed, because a statement is inlined into every card its anchor
    # reaches and each generated file needs its own. Shown everything, a model
    # told not to repeat itself suppresses the second one.
    fake = FakeTransport()
    elsewhere = (
        Assertion(
            anchor=Anchor.parse("primitive:invoice_complete"),
            kind="terminology",
            statement="An identifier matches after trimming spaces.",
        ),
    )
    thread = Thread(
        category="ambiguous_rule",
        question="How do you compare them?",
        anchors=(Anchor.parse("primitive:coas_valid"),),
    )

    await distil(seed, thread, elsewhere, transport=fake)

    assert "An identifier matches" not in _shown(fake)


async def test_a_board_level_statement_reaches_every_conversation(seed: Board):
    # `reaches` says a board anchor bears on every card, and the distiller has to
    # agree with the freeze about that or it restates a rule already inlined.
    fake = FakeTransport()
    everywhere = (
        Assertion(
            anchor=Anchor.parse("board"),
            kind="rule",
            statement="One container is one shipment.",
        ),
    )
    thread = Thread(
        category="ambiguous_rule",
        question="How do you compare them?",
        anchors=(Anchor.parse("primitive:coas_valid"),),
    )

    await distil(seed, thread, everywhere, transport=fake)

    assert "One container is one shipment" in _shown(fake)


async def test_nothing_recorded_yet_is_not_an_error(seed: Board):
    fake = FakeTransport()
    thread = Thread(
        category="ambiguous_rule",
        question="How do you compare them?",
        anchors=(Anchor.parse("primitive:coas_valid"),),
    )

    assert await distil(seed, thread, transport=fake) == ()
