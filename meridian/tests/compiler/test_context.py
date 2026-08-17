"""Tests for the transposition.

What matters here is what a card does **not** receive. Every statement reaching
every card would be simpler and useless — codegen would read the whole board's
worth of rules to write one predicate, and a claim about certificates would sit
in a file that only handles invoices.

So most of these assert an absence.
"""

import uuid

from meridian.compiler.context import context_for, reaches
from meridian.domain.graph import Board
from meridian.domain.review import Anchor, Assertion

TH_ONE = uuid.uuid4()
TH_TWO = uuid.uuid4()

ONE_SHIPMENT = Assertion(
    thread_id=TH_ONE,
    anchor=Anchor(kind="board"),
    kind="rule",
    statement="one container is one shipment",
)
BATCH_FORMAT = Assertion(
    thread_id=TH_ONE,
    anchor=Anchor.parse("entity_field:commercial_invoice.batch_no"),
    kind="constraint",
    statement=r"batch numbers match ^[A-Z]{4}\d{5}$",
    constraint_json={"pattern": r"^[A-Z]{4}\d{5}$"},
)
RERUN = Assertion(
    thread_id=TH_TWO,
    anchor=Anchor.parse("primitive:coas_valid"),
    kind="exception",
    statement="re-run every batch when a corrected COA arrives",
)
ORPHAN_COA = Assertion(
    anchor=Anchor(kind="board"),
    kind="negative",
    statement="a COA whose batch is on no invoice line belongs to another shipment",
)
WAIT = Assertion(
    anchor=Anchor.parse("edge:e5"),
    kind="timing",
    statement="wait 48 hours, then escalate",
)

SETTLED = (ONE_SHIPMENT, BATCH_FORMAT, RERUN, ORPHAN_COA, WAIT)


# --- what reaches what -----------------------------------------------------


def test_a_board_statement_reaches_every_card(seed: Board):
    for card in seed.nodes():
        assert reaches(card, Anchor(kind="board"))


def test_a_statement_about_one_card_reaches_only_that_card(seed: Board):
    anchor = Anchor.parse("primitive:coas_valid")
    reached = [c.key for c in seed.nodes() if reaches(c, anchor)]
    assert reached == ["coas_valid"]


def test_a_field_statement_travels_sideways_to_whoever_reads_that_entity(seed: Board):
    # The point of the entity_field anchor: a constraint on the invoice's batch
    # numbers lands on the checks that test them, and nowhere else.
    anchor = Anchor.parse("entity_field:commercial_invoice.batch_no")
    reached = {c.key for c in seed.nodes() if reaches(c, anchor)}
    assert "coas_valid" in reached
    assert "invoice_complete" in reached
    # The terminal reads nothing at all, which is the only honest negative on
    # this board: every reporting step quotes the invoice number, so every one
    # of them is entitled to know how batch numbers are written.
    assert "documentation_validated" not in reached


def test_an_edge_statement_reaches_no_card(seed: Board):
    # A claim about a transition belongs to the transition. Putting it inside a
    # step's file would tell that step about something it does not do.
    assert not any(reaches(c, Anchor.parse("edge:e5")) for c in seed.nodes())


def test_a_group_statement_reaches_only_its_members(seed: Board):
    grouped = seed.p("coas_valid").model_copy(update={"group_key": "validation"})
    board = seed.model_copy(
        update={
            "primitives": tuple(grouped if p.key == "coas_valid" else p for p in seed.primitives)
        }
    )
    anchor = Anchor.parse("group:validation")
    assert {c.key for c in board.nodes() if reaches(c, anchor)} == {"coas_valid"}


# --- what a card ends up carrying ------------------------------------------


def test_a_card_separates_what_it_inherited_from_what_was_said_about_it(seed: Board):
    context = context_for(seed, SETTLED, "coas_valid")
    assert "[rule] one container is one shipment" in context.inherited
    assert "[constraint] batch numbers match ^[A-Z]{4}\\d{5}$" in context.inherited
    assert context.local == ("[exception] re-run every batch when a corrected COA arrives",)


def test_a_card_does_not_receive_a_constraint_on_an_entity_it_never_reads(seed: Board):
    # documentation_validated is a named end state and reads nothing.
    context = context_for(seed, SETTLED, "documentation_validated")
    assert not any("batch numbers match" in line for line in context.inherited)
    # A board-level statement still reaches it, which is what makes this a test
    # of the entity filter rather than of the card having no context at all.
    assert "[rule] one container is one shipment" in context.inherited


def test_a_card_never_receives_another_cards_local_statement(seed: Board):
    context = context_for(seed, SETTLED, "invoice_complete")
    assert context.local == ()
    assert not any("corrected COA" in line for line in context.inherited)


def test_negative_knowledge_is_collected_at_every_level(seed: Board):
    # It states something about the world rather than about a step, so it does
    # not stop being true lower down and is never overridden.
    for key in ("coas_valid", "invoice_complete", "report_coa_discrepancy"):
        assert context_for(seed, SETTLED, key).negative == (ORPHAN_COA.statement,)


def test_a_negative_is_never_prefixed_with_its_kind(seed: Board):
    # The other lists are tagged so codegen can weight a rule against a piece of
    # terminology. A negative is just a fact, and reads as one.
    (only,) = context_for(seed, SETTLED, "coas_valid").negative
    assert not only.startswith("[")


def test_a_superseded_statement_does_not_reach_the_spec(seed: Board):
    replaced = RERUN.model_copy(update={"superseded_by": uuid.uuid4()})
    context = context_for(seed, (ONE_SHIPMENT, replaced), "coas_valid")
    assert context.local == ()


def test_provenance_points_back_at_the_conversations(seed: Board):
    context = context_for(seed, SETTLED, "coas_valid")
    assert set(context.provenance) == {str(TH_ONE), str(TH_TWO)}


def test_a_card_review_settled_nothing_about_carries_nothing(seed: Board):
    assert context_for(seed, (), "coas_valid").is_empty()


# --- the property a checksum depends on ------------------------------------


def test_the_same_board_and_statements_produce_the_same_context(seed: Board):
    # Two freezes of an unchanged board must agree exactly, and they cannot if
    # this ordering depends on the order rows came back from the database.
    forwards = context_for(seed, SETTLED, "coas_valid")
    backwards = context_for(seed, tuple(reversed(SETTLED)), "coas_valid")
    assert forwards == backwards


def test_broader_statements_come_first(seed: Board):
    # Narrow beats broad on conflict, so a reader — human or model — should meet
    # the general rule before the exception to it.
    context = context_for(seed, SETTLED, "coas_valid")
    assert context.inherited[0].startswith("[rule]")


def test_a_board_statement_appears_on_every_card_that_inherits_it(seed: Board):
    # Inlined rather than referenced: the payload repeats itself so that codegen
    # reads one entry and joins nothing.
    everywhere = [context_for(seed, SETTLED, c.key).inherited for c in seed.nodes()]
    assert all("[rule] one container is one shipment" in lines for lines in everywhere)
