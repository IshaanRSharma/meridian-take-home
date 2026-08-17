"""Reading something nothing has produced yet.

The only reference rule that depends on the *flow* rather than on the cards.
Every other one asks whether a thing exists on the board; this asks whether it
exists **yet**.

It stayed invisible for as long as every entity arrived with the Event, because
an Event is upstream of everything. Two independent attempts to build a board on
a different vocabulary — one adding a lookup, one adding a check that fills an
output row — both produced boards that lint completely clean while reading
nothing forever. These tests are those two boards.
"""

import pytest

from meridian.compiler import rules
from meridian.domain.graph import (
    ActionPrimitive,
    Board,
    CheckPrimitive,
    Edge,
    EntityPrimitive,
    EventPrimitive,
)
from meridian.domain.primitives import (
    ActionConfig,
    CheckConfig,
    Criterion,
    EntityConfig,
    EventConfig,
    FieldRef,
    Fill,
    Outcome,
    Timing,
)


def _board(*, reader_first: bool) -> Board:
    """A lookup that produces a record, and a check that reads it.

    ``reader_first`` swaps the two so the check runs before anything has
    fetched what it reads.
    """
    arrival = EventPrimitive(
        key="request_arrived",
        config=EventConfig(
            name="Request arrived",
            channel="email",
            correlation_key=FieldRef.parse("request.reference"),
            match_condition="subject contains 'request'",
            captures=("request",),
            timing=Timing(kind="await"),
        ),
    )
    lookup = ActionPrimitive(
        key="verify_reference",
        config=ActionConfig(
            name="Verify the reference",
            effect="lookup",
            system="the terminal's system",
            produces="terminal_record",
            on_failure="wait",
            timeout="PT30S",
        ),
    )
    check = CheckPrimitive(
        key="reference_known",
        config=CheckConfig(
            name="Is the reference known?",
            criteria=(Criterion(op="present", left=FieldRef.parse("terminal_record.reference")),),
            scope="per_case",
            inputs=("terminal_record",),
            outcomes=(Outcome(name="pass"), Outcome(name="unknown", priority=1)),
            evidence=(FieldRef.parse("terminal_record.reference"),),
            on_missing_input="fail",
        ),
    )
    done = ActionPrimitive(
        key="accepted", config=ActionConfig(name="Accepted", effect="noop", is_terminal=True)
    )
    entities = (
        EntityPrimitive(
            key="request",
            config=EntityConfig(
                name="Request",
                identified_by="the subject line",
                fields={"reference": {"type": "string"}},
            ),
        ),
        EntityPrimitive(
            key="terminal_record",
            # No `identified_by`: a looked-up record has nothing to recognise.
            config=EntityConfig(name="Terminal record", fields={"reference": {"type": "string"}}),
        ),
    )

    order = (
        ("reference_known", "verify_reference")
        if reader_first
        else (
            "verify_reference",
            "reference_known",
        )
    )
    return Board(
        name="reference check",
        primitives=(arrival, lookup, check, done, *entities),
        edges=(
            Edge(key="e1", from_key="request_arrived", to_key=order[0]),
            Edge(key="e2", from_key=order[0], to_key=order[1]),
            Edge(key="e3", from_key="reference_known", to_key="accepted", on_outcomes=("pass",)),
            Edge(key="e4", from_key="reference_known", to_key="accepted", on_outcomes=("unknown",)),
        ),
    )


def test_a_lookup_placed_after_its_reader_is_caught():
    # The board a subagent built to falsify the compiler. Every reference
    # resolves, every outcome is wired, and the check reads an empty record
    # forever.
    found = [f for f in rules.blocking(_board(reader_first=True)) if f.field == "inputs"]
    assert [f.anchor for f in found] == ["primitive:reference_known"]
    assert "nothing produces it before this step runs" in found[0].reason


def test_the_same_board_in_the_right_order_is_clean():
    ordered = _board(reader_first=False)
    assert [f for f in rules.blocking(ordered) if f.field == "inputs"] == []


def test_the_finding_names_the_card_in_the_owners_words():
    # "Terminal record", not "terminal_record". The reason is the text rendered
    # on the card.
    (found,) = [f for f in rules.blocking(_board(reader_first=True)) if f.field == "inputs"]
    assert "Terminal record" in found.reason


def test_a_check_reading_a_row_another_check_fills_must_come_after_it(seed: Board):
    # The second way in, found independently: `Board.producers_of` cannot see a
    # Check that fills an output entity, because a Check declares it produces
    # nothing — true of what it reads, false of the row it writes.
    summary = EntityPrimitive(
        key="summary",
        config=EntityConfig(name="Summary", fields={"failed": {"type": "integer"}}),
    )
    filler = seed.p("invoice_complete")
    board = seed.model_copy(
        update={
            "primitives": (
                *seed.primitives,
                summary,
                CheckPrimitive(
                    key="too_many_failures",
                    config=CheckConfig(
                        name="Are there too many failures?",
                        criteria=(Criterion(op="present", left=FieldRef.parse("summary.failed")),),
                        scope="per_case",
                        inputs=("summary",),
                        outcomes=(Outcome(name="pass"), Outcome(name="escalate", priority=1)),
                        on_missing_input="fail",
                    ),
                ),
            ),
            "edges": (
                # Deliberately BEFORE the check that fills it.
                Edge(key="e0", from_key="prealert_received", to_key="too_many_failures"),
                *seed.edges,
            ),
        }
    )
    board = board.model_copy(
        update={
            "primitives": tuple(
                p.model_copy(
                    update={
                        "config": p.config.model_copy(
                            update={
                                "fills": (
                                    Fill(
                                        measure="failed",
                                        field=FieldRef.parse("summary.failed"),
                                    ),
                                )
                            }
                        )
                    }
                )
                if p.key == filler.key
                else p
                for p in board.primitives
            )
        }
    )

    anchors = [f.anchor for f in rules.blocking(board) if f.field == "inputs"]
    assert "primitive:too_many_failures" in anchors


@pytest.mark.parametrize("reader_first", [True, False])
def test_an_entity_nobody_produces_is_left_to_another_rule(reader_first: bool):
    # "Nothing produces it at all" is a different problem with a different fix,
    # and `inputs_exist` already owns it. Reporting both would show the process
    # owner two blanks for one mistake.
    board = _board(reader_first=reader_first)
    stripped = board.model_copy(
        update={"primitives": tuple(p for p in board.primitives if p.key != "verify_reference")}
    )
    reasons = [f.reason for f in rules.inputs_arrive_before_they_are_read(stripped)]
    assert not any("nothing produces it before" in r for r in reasons)
