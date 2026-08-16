"""Tests for board traversal.

Two properties run through all of this. Entities are never traversed — they are
referenced, so every walk over the graph must skip them. And the graph is not a
DAG: a ``repeat`` edge sends execution back to work that already ran, so
traversal terminates by bounding itself rather than by assuming acyclicity.

``test_the_seed_board_reports_exactly_its_documented_gaps`` is the floor. It
holds the seeded board to the gap list written in its own header, so "the
reviewer will catch these" is checked rather than claimed.
"""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from meridian.domain.errors import NotFoundError
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
    Cardinality,
    CheckConfig,
    Criterion,
    EntityConfig,
    EventConfig,
    FieldRef,
    Outcome,
)

PASS = Outcome(name="pass")
FAIL = Outcome(name="fail")


def anchors(board: Board) -> set[str]:
    return {f"{f.anchor}:{f.field}" for f in board.findings()}


@pytest.fixture
def tiny() -> Board:
    """Event → Check → terminal Action, with the failure path drawn."""
    return Board(
        name="tiny",
        primitives=[
            EventPrimitive(key="arrived", config=EventConfig(name="Arrived")),
            CheckPrimitive(
                key="looks_ok",
                config=CheckConfig(name="Looks ok?", outcomes=[PASS, FAIL]),
            ),
            ActionPrimitive(key="done", config=ActionConfig(name="Done", is_terminal=True)),
            ActionPrimitive(key="complain", config=ActionConfig(name="Complain", is_terminal=True)),
        ],
        edges=[
            Edge(key="e1", from_key="arrived", to_key="looks_ok"),
            Edge(key="e2", from_key="looks_ok", to_key="done", on_outcomes=["pass"]),
            Edge(
                key="e3",
                from_key="looks_ok",
                to_key="complain",
                relation="exception",
                on_outcomes=["fail"],
            ),
        ],
    )


# --- construction ----------------------------------------------------------


def test_duplicate_primitive_keys_are_rejected():
    with pytest.raises(ValidationError):
        Board(
            name="b",
            primitives=[EventPrimitive(key="same"), ActionPrimitive(key="same")],
        )


def test_duplicate_edge_keys_are_rejected():
    with pytest.raises(ValidationError):
        Board(
            name="b",
            primitives=[EventPrimitive(key="aa"), ActionPrimitive(key="bb")],
            edges=[
                Edge(key="ee", from_key="aa", to_key="bb"),
                Edge(key="ee", from_key="bb", to_key="aa"),
            ],
        )


def test_a_self_edge_is_rejected_unless_it_is_a_repeat():
    with pytest.raises(ValidationError):
        Edge(key="ee", from_key="aa", to_key="aa")


def test_a_repeat_self_edge_is_allowed():
    assert Edge(key="ee", from_key="aa", to_key="aa", relation="repeat").relation == "repeat"


# --- steps versus entities -------------------------------------------------


def test_steps_exclude_entities(tiny: Board):
    board = tiny.model_copy(update={"primitives": (*tiny.primitives, EntityPrimitive(key="thing"))})
    assert "thing" not in {s.key for s in board.steps()}
    assert [e.key for e in board.entities()] == ["thing"]


def test_an_entity_is_never_a_terminal_despite_having_no_edges(tiny: Board):
    board = tiny.model_copy(update={"primitives": (*tiny.primitives, EntityPrimitive(key="thing"))})
    assert "thing" not in {s.key for s in board.terminals()}


def test_entities_are_excluded_from_reachability(tiny: Board):
    board = tiny.model_copy(update={"primitives": (*tiny.primitives, EntityPrimitive(key="thing"))})
    assert "thing" not in board.reachable_from_events()


def test_an_edge_pointing_at_an_entity_is_a_finding():
    board = Board(
        name="b",
        primitives=[EventPrimitive(key="arrived"), EntityPrimitive(key="thing")],
        edges=[Edge(key="e1", from_key="arrived", to_key="thing")],
    )
    assert "edge:e1:to_key" in anchors(board)


# --- lookup ----------------------------------------------------------------


def test_p_returns_the_card(tiny: Board):
    assert tiny.p("looks_ok").primitive_type == "check"


def test_p_raises_not_found_for_an_unknown_key(tiny: Board):
    with pytest.raises(NotFoundError):
        tiny.p("nope")


def test_events_actions_and_checks_partition_the_steps(tiny: Board):
    counted = len(tiny.events()) + len(tiny.actions()) + len(tiny.checks())
    assert counted == len(tiny.steps())


# --- traversal -------------------------------------------------------------


def test_outgoing_and_incoming(tiny: Board):
    assert {e.key for e in tiny.outgoing("looks_ok")} == {"e2", "e3"}
    assert {e.key for e in tiny.incoming("looks_ok")} == {"e1"}


def test_terminals_are_steps_with_no_outgoing_edge(tiny: Board):
    assert {s.key for s in tiny.terminals()} == {"done", "complain"}


def test_reachable_from_events(tiny: Board):
    assert tiny.reachable_from_events() == frozenset({"arrived", "looks_ok", "done", "complain"})


def test_a_step_no_event_reaches_is_unreachable(tiny: Board):
    board = tiny.model_copy(
        update={"primitives": (*tiny.primitives, ActionPrimitive(key="orphan"))}
    )
    assert {s.key for s in board.unreachable()} == {"orphan"}


def test_a_repeat_edge_does_not_make_traversal_diverge(tiny: Board):
    # The board is a state machine, not a DAG. A corrected document sends
    # execution back to a check that already ran, and reachability has to
    # terminate anyway.
    board = tiny.model_copy(
        update={
            "edges": (
                *tiny.edges,
                Edge(key="e4", from_key="complain", to_key="looks_ok", relation="repeat"),
            )
        }
    )
    assert board.reachable_from_events() == frozenset({"arrived", "looks_ok", "done", "complain"})
    assert "looks_ok" in board.downstream("complain")


def test_upstream_is_transitive(tiny: Board):
    assert tiny.upstream("done") == frozenset({"looks_ok", "arrived"})


# --- outcomes --------------------------------------------------------------


def test_outcomes_report_where_each_one_goes(tiny: Board):
    wiring = {w.name: w for w in tiny.outcomes("looks_ok")}
    assert wiring["pass"].wired
    assert wiring["pass"].to == ("done",)
    assert wiring["fail"].wired


def test_three_outcomes_and_two_edges_leaves_one_unwired(tiny: Board):
    check = CheckPrimitive(
        key="looks_ok",
        config=CheckConfig(name="Looks ok?", outcomes=[PASS, FAIL, Outcome(name="odd")]),
    )
    board = tiny.model_copy(
        update={"primitives": (tiny.primitives[0], check, *tiny.primitives[2:])}
    )
    unwired = [w.name for w in board.outcomes("looks_ok") if not w.wired]
    assert unwired == ["odd"]
    assert "primitive:looks_ok:outcomes" in anchors(board)


def test_an_edge_carrying_an_undeclared_outcome_is_a_finding(tiny: Board):
    board = tiny.model_copy(
        update={
            "edges": (
                tiny.edges[0],
                Edge(key="e2", from_key="looks_ok", to_key="done", on_outcomes=["invented"]),
                tiny.edges[2],
            )
        }
    )
    assert "edge:e2:on_outcomes" in anchors(board)


# --- entities --------------------------------------------------------------


def test_readers_of_covers_captures_and_inputs():
    board = Board(
        name="b",
        primitives=[
            EntityPrimitive(
                key="invoice",
                config=EntityConfig(name="Invoice", identified_by="header", fields={"no": {}}),
            ),
            EventPrimitive(key="arrived", config=EventConfig(name="Arrived", captures=["invoice"])),
            CheckPrimitive(key="ck", config=CheckConfig(name="Ck", inputs=["invoice"])),
        ],
    )
    assert {s.key for s in board.readers_of("invoice")} == {"arrived", "ck"}


def test_a_lookup_is_a_producer_of_the_entity_it_names():
    board = Board(
        name="b",
        primitives=[
            EntityPrimitive(key="record", config=EntityConfig(name="Record")),
            ActionPrimitive(
                key="verify",
                config=ActionConfig(name="Verify", effect="lookup", produces="record"),
            ),
        ],
    )
    assert {s.key for s in board.producers_of("record")} == {"verify"}


def test_an_entity_nobody_reads_is_a_finding():
    board = Board(
        name="b",
        primitives=[
            EntityPrimitive(
                key="unused",
                config=EntityConfig(name="Unused", identified_by="x", fields={"a": {}}),
            )
        ],
    )
    assert "primitive:unused:name" in anchors(board)


def test_a_step_reading_an_entity_that_is_not_on_the_board_is_a_finding():
    board = Board(
        name="b",
        primitives=[CheckPrimitive(key="ck", config=CheckConfig(name="Ck", inputs=["ghost"]))],
    )
    assert "primitive:ck:inputs" in anchors(board)


def test_a_field_reference_the_entity_does_not_carry_is_a_finding():
    board = Board(
        name="b",
        primitives=[
            EntityPrimitive(
                key="invoice",
                config=EntityConfig(name="Invoice", identified_by="h", fields={"invoice_no": {}}),
            ),
            CheckPrimitive(
                key="ck",
                config=CheckConfig(
                    name="Ck",
                    inputs=["invoice"],
                    criteria=[
                        Criterion(op="present", left=FieldRef(entity="invoice", path="missing_no"))
                    ],
                ),
            ),
        ],
    )
    assert "primitive:ck:criteria" in anchors(board)


def test_cardinality_per_must_resolve_to_a_declared_field():
    board = Board(
        name="b",
        primitives=[
            EntityPrimitive(
                key="invoice",
                config=EntityConfig(name="Invoice", identified_by="h", fields={"invoice_no": {}}),
            ),
            EntityPrimitive(
                key="coa",
                config=EntityConfig(
                    name="COA",
                    identified_by="h",
                    fields={"batch_no": {}},
                    cardinality=Cardinality(
                        kind="one_per", per=FieldRef(entity="invoice", path="line_items[].batch_no")
                    ),
                ),
            ),
            CheckPrimitive(key="ck", config=CheckConfig(name="Ck", inputs=["invoice", "coa"])),
        ],
    )
    assert "primitive:coa:cardinality.per" in anchors(board)


# --- flow shape ------------------------------------------------------------


def test_an_event_with_nothing_after_it_is_a_finding():
    board = Board(name="b", primitives=[EventPrimitive(key="arrived")])
    assert "primitive:arrived:outgoing" in anchors(board)


def test_a_dead_end_that_is_not_marked_as_an_ending_is_a_finding(tiny: Board):
    board = tiny.model_copy(
        update={
            "primitives": (
                *tiny.primitives[:3],
                ActionPrimitive(key="complain", config=ActionConfig(name="Complain")),
            )
        }
    )
    assert "primitive:complain:outgoing" in anchors(board)


# --- dry run ---------------------------------------------------------------


def test_dry_run_reaches_a_terminal_on_the_happy_path(tiny: Board):
    run = tiny.dry_run({"looks_ok": "pass"})
    assert run.result == "reached_terminal"
    assert [s.key for s in run.trace] == ["arrived", "looks_ok", "done"]


def test_dry_run_reports_what_a_dead_end_never_reached(tiny: Board):
    board = tiny.model_copy(
        update={
            "primitives": (
                *tiny.primitives[:3],
                ActionPrimitive(key="complain", config=ActionConfig(name="Complain")),
            )
        }
    )
    run = board.dry_run({"looks_ok": "fail"})
    assert run.result == "dead_end"
    assert "done" in run.unreached


def test_dry_run_reports_an_undefined_branch_for_an_unwired_outcome(tiny: Board):
    check = CheckPrimitive(
        key="looks_ok",
        config=CheckConfig(name="Looks ok?", outcomes=[PASS, FAIL, Outcome(name="odd")]),
    )
    board = tiny.model_copy(
        update={"primitives": (tiny.primitives[0], check, *tiny.primitives[2:])}
    )
    run = board.dry_run({"looks_ok": "odd"})
    assert run.result == "undefined_branch"


def test_dry_run_bounds_itself_on_a_cycle(tiny: Board):
    board = tiny.model_copy(
        update={
            "edges": (
                *tiny.edges,
                Edge(key="e4", from_key="complain", to_key="looks_ok", relation="repeat"),
            )
        }
    )
    complain = ActionPrimitive(key="complain", config=ActionConfig(name="Complain"))
    board = board.model_copy(update={"primitives": (*board.primitives[:3], complain)})
    run = board.dry_run({"looks_ok": "fail"})
    assert run.result == "loop"


# --- the seed board --------------------------------------------------------


@pytest.fixture
def seed(repo_root: Path) -> Board:
    raw = json.loads((repo_root / "db" / "seeds" / "prealert_board.json").read_text())
    return Board(
        name=raw["board"]["name"],
        status=raw["board"]["status"],
        review_round=raw["board"]["review_round"],
        primitives=raw["primitives"],
        edges=raw["edges"],
        layout=raw["layout"],
    )


def test_the_seed_board_loads_and_validates(seed: Board):
    assert len(seed.steps()) == 6
    assert len(seed.entities()) == 2


def test_entities_carry_no_layout_position(seed: Board):
    # The whole implementation of "first-class but not drawn".
    assert not {e.key for e in seed.entities()} & set(seed.layout)


def test_the_seed_board_reports_exactly_its_documented_gaps(seed: Board):
    blocking = {f"{f.anchor}:{f.field}" for f in seed.findings() if f.severity == "blocking"}
    assert blocking == {
        "primitive:report_coa_discrepancy:recipients",  # the SOP names nobody
        "primitive:report_invoice_discrepancy:system",  # "log an error" — where?
        "primitive:coas_valid:outcomes",  # mismatched_coa goes nowhere
    }


def test_the_seed_boards_softer_gaps_are_important_not_blocking(seed: Board):
    # A board with these can still freeze. They are worth asking about, and
    # ranking them below the blocking three is what keeps the list readable.
    important = {f"{f.anchor}:{f.field}" for f in seed.findings() if f.severity == "important"}
    assert {
        "primitive:invoice_complete:on_missing_input",
        "primitive:coas_valid:on_missing_input",
        "primitive:report_coa_discrepancy:idempotency_key",
        "primitive:report_invoice_discrepancy:idempotency_key",
    } <= important


def test_the_seed_board_leaves_mismatched_coa_unwired(seed: Board):
    unwired = [w.name for w in seed.outcomes("coas_valid") if not w.wired]
    assert unwired == ["mismatched_coa"]


def test_the_seed_board_dead_ends_when_the_invoice_fails(seed: Board):
    # The finding that the corpus later confirms: CAAU4056270 failed its invoice
    # check and still counted 5 of 5 COAs, so the COA check cannot really be
    # unreachable from an invoice failure.
    run = seed.dry_run({"invoice_complete": "missing_information"})
    assert run.result == "dead_end"
    assert "coas_valid" in run.unreached
