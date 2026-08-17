"""Tests for what the reviewer may do besides read.

Two tools, and both exist for the same reason: the model may not assert
something it did not find out. `dry_run` stops it claiming a situation ends
somewhere, and `where_values_meet` stops it having to notice, from five
unrelated corners of the configs, that two values are supposed to be the same
thing.

Everything here runs offline against the real board — a tool that described a
board rather than interrogating this one would be worse than no tool at all.
"""

from uuid import UUID

from meridian.domain.graph import Board
from meridian.domain.primitives import Cardinality, Criterion, FieldRef, Operand
from meridian.domain.review import Anchor, Assertion
from meridian.reviewer import tools


def walking(board: Board):
    """The `dry_run` tool, by name rather than by position."""
    return next(t for t in tools.for_board(board, {}) if t.name == "dry_run")


def matching(board: Board, settled=()):
    return next(t for t in tools.for_board(board, {}, settled) if t.name == "where_values_meet")


# --- walking a situation the model invented ----------------------------------


async def test_the_tool_walks_the_real_board(seed: Board):
    # The tool is the only thing stopping the model asserting a path the board
    # does not have, so it has to walk THIS board rather than describe one.
    tool = walking(seed)
    walked = await tool.run(
        {
            "outcomes": [
                {"check": "invoice_complete", "answers": ["pass"]},
                {"check": "coas_valid", "answers": ["pass"]},
            ]
        }
    )

    assert walked["result"] == "reached_terminal"
    assert walked["path"].endswith("documentation_validated")


async def test_the_tool_reports_a_bad_situation_rather_than_raising(seed: Board):
    # The model will get an outcome name wrong. That has to come back as an
    # answer it can learn from, not an exception that kills the round.
    tool = walking(seed)
    walked = await tool.run({"outcomes": [{"check": "coas_valid", "answers": ["no_such_outcome"]}]})

    assert walked["result"] == "undefined_branch"


async def test_the_tool_hands_back_something_worth_reasoning_about(seed: Board):
    # A bare list of step keys tells the model where the walk ended and nothing
    # about why, so it has no reason to walk again. What makes investigating
    # possible is getting back the shape: the arrows named, what each step on the
    # path actually reads, and what the walk never got to.
    tool = walking(seed)
    walked = await tool.run(
        {"outcomes": [{"check": "invoice_complete", "answers": ["missing_information"]}]}
    )

    assert "──e3 on missing_information (exception)──▶" in walked["path"]
    assert any("tests" in line for line in walked["along_the_way"])
    assert "coas_valid" in walked["never_reached"]
    assert walked["why_it_stopped"] == "no outgoing edges"


async def test_the_tool_can_answer_one_check_differently_each_time(seed: Board):
    # A correction resubmitted is the situation the whole product exists for, and
    # the model has to be able to describe it: wrong on Tuesday, right on
    # Thursday. Several answers for one check is how.
    tool = walking(seed)
    walked = await tool.run(
        {"outcomes": [{"check": "coas_valid", "answers": ["missing_coa", "pass"]}]}
    )

    assert walked["result"] in {"dead_end", "reached_terminal", "undefined_branch"}


# --- where two values have to be the same thing ------------------------------
#
# The point of this tool is that these five shapes live in five unrelated
# corners of the schema. A model reading configs one at a time sees a matching
# criterion, then much later a cardinality, then an event's correlation key, and
# has no reason to recognise them as the same question asked three ways.


def points(board: Board, settled=()):
    return {(p["how"], tuple(p["sides"])): p for p in tools.where_values_meet(board, settled)}


def test_a_check_that_pairs_two_sets_of_things_is_a_meeting_point(seed: Board):
    found = points(seed)[
        (
            "each_has_matching",
            ("commercial_invoice.line_items[].batch_no", "certificate_of_analysis.batch_no"),
        )
    ]

    assert found["where"] == "primitive:coas_valid"


def test_a_count_of_one_thing_per_another_is_a_meeting_point(seed: Board):
    # "One certificate per batch on the invoice" is a relationship between two
    # entities wearing the clothes of a count, and it is the reason the expected
    # number is derivable at all. Nothing calls it a comparison.
    found = points(seed)[
        ("one_per", ("certificate_of_analysis", "commercial_invoice.line_items[].batch_no"))
    ]

    assert found["where"] == "primitive:certificate_of_analysis"


def test_the_value_everything_is_filed_under_is_a_meeting_point(seed: Board):
    # The largest join on any board: every thing that arrives has to be
    # recognised as belonging to the case already in progress. It appears
    # nowhere as a comparison, which is exactly why it goes unasked.
    found = points(seed)[("correlation_key", ("commercial_invoice.container_no",))]

    assert found["where"] == "primitive:prealert_received"


def test_a_check_for_a_value_being_there_at_all_is_not_a_meeting_point(seed: Board):
    # `invoice_complete` tests four fields for presence. One side, nothing to
    # recognise as the same thing as anything else — and four false positives on
    # a board this size would bury the three real ones.
    found = tools.where_values_meet(seed)
    assert not [p for p in found if p["where"] == "primitive:invoice_complete"]


def test_nothing_said_about_a_meeting_point_is_the_finding(seed: Board):
    # Every one on the seed board. A rule missing from a *relationship* has no
    # field to be missing from, so no lint rule can see it.
    assert all(p["settled"] == [] for p in tools.where_values_meet(seed))


def test_a_rule_about_one_side_shows_up_against_the_join_it_settles(seed: Board):
    settled = (
        Assertion(
            anchor=Anchor.parse("entity_field:certificate_of_analysis.batch_no"),
            kind="terminology",
            statement="Association is by product code, not page order.",
        ),
    )

    found = points(seed, settled)[
        (
            "each_has_matching",
            ("commercial_invoice.line_items[].batch_no", "certificate_of_analysis.batch_no"),
        )
    ]

    assert found["settled"] == ["[terminology] Association is by product code, not page order."]


def test_a_rule_about_an_unrelated_field_stays_where_it_belongs(seed: Board):
    settled = (
        Assertion(
            anchor=Anchor.parse("entity_field:commercial_invoice.container_no"),
            kind="rule",
            statement="One container is one shipment.",
        ),
    )

    found = tools.where_values_meet(seed, settled)
    against_the_key = next(p for p in found if p["how"] == "correlation_key")
    against_the_match = next(p for p in found if p["how"] == "each_has_matching")

    assert against_the_key["settled"] == ["[rule] One container is one shipment."]
    assert against_the_match["settled"] == []


def test_a_statement_no_longer_current_is_not_shown(seed: Board):
    settled = (
        Assertion(
            anchor=Anchor.parse("entity_field:certificate_of_analysis.batch_no"),
            kind="terminology",
            statement="An answer that has since been replaced.",
            superseded_by=UUID("11111111-1111-1111-1111-111111111111"),
        ),
    )

    assert all(p["settled"] == [] for p in tools.where_values_meet(seed, settled))


# --- the two shapes the seed board does not happen to have -------------------


def checking(board: Board, *criteria: Criterion) -> Board:
    """The seed board with `coas_valid` testing something else instead."""
    return board.model_copy(
        update={
            "primitives": tuple(
                card.model_copy(
                    update={"config": card.config.model_copy(update={"criteria": criteria})}
                )
                if card.key == "coas_valid"
                else card
                for card in board.primitives
            )
        }
    )


def test_comparing_one_field_against_another_is_a_meeting_point(seed: Board):
    board = checking(
        seed,
        Criterion(
            op="compare",
            left=FieldRef.parse("commercial_invoice.container_no"),
            operator="eq",
            right=Operand(kind="field", field=FieldRef.parse("prealert_email.subject")),
        ),
    )

    found = points(board)[
        ("compare", ("commercial_invoice.container_no", "prealert_email.subject"))
    ]

    assert found["compared_by"] == "eq"


def test_comparing_a_field_against_a_fixed_value_is_not(seed: Board):
    # One side is a constant, so there is nothing to recognise as the same thing
    # as anything else. Including these would bury the real joins in noise.
    board = checking(
        seed,
        Criterion(
            op="compare",
            left=FieldRef.parse("commercial_invoice.container_no"),
            operator="matches",
            right=Operand(kind="value", value="^[A-Z]{4}"),
        ),
    )

    assert not [p for p in tools.where_values_meet(board) if p["where"] == "primitive:coas_valid"]


def test_a_rule_written_in_words_across_two_things_is_a_meeting_point(seed: Board):
    # The escape hatch is where the most interesting matches end up, precisely
    # because they were too awkward to type. It still names the fields it reads,
    # so reading across two entities is as detectable here as anywhere else.
    board = checking(
        seed,
        Criterion(
            op="custom",
            statement="The certificate has to be for the product on the invoice line.",
            reads=(
                FieldRef.parse("commercial_invoice.line_items[].fda_product_code"),
                FieldRef.parse("certificate_of_analysis.batch_no"),
            ),
        ),
    )

    found = next(p for p in tools.where_values_meet(board) if p["how"] == "described_in_words")

    assert found["the_owner_wrote"].startswith("The certificate has to be")


def test_a_rule_in_words_about_one_thing_only_is_not(seed: Board):
    board = checking(
        seed,
        Criterion(
            op="custom",
            statement="Every line has to carry a code.",
            reads=(
                FieldRef.parse("commercial_invoice.line_items[].fda_product_code"),
                FieldRef.parse("commercial_invoice.line_items[].ndc_number"),
            ),
        ),
    )

    assert not [p for p in tools.where_values_meet(board) if p["how"] == "described_in_words"]


def test_only_a_count_against_another_thing_is_a_join(seed: Board):
    # `per` is required for `one_per` by a finding rather than a validator, so a
    # half-edited card can carry a stray one under a different kind. That is not
    # a relationship anyone has to recognise, and offering it as one sends the
    # owner a question about a field they already stopped using.
    board = seed.model_copy(
        update={
            "primitives": tuple(
                card.model_copy(
                    update={
                        "config": card.config.model_copy(
                            update={
                                "cardinality": Cardinality(
                                    kind="many",
                                    per=FieldRef.parse("commercial_invoice.line_items[].batch_no"),
                                )
                            }
                        )
                    }
                )
                if card.key == "certificate_of_analysis"
                else card
                for card in seed.primitives
            )
        }
    )

    assert not [p for p in tools.where_values_meet(board) if p["how"] == "one_per"]
