"""Tests for board traversal.

Two properties run through all of this. Entities are never traversed — they are
referenced, so every walk over the graph must skip them. And the graph is not a
DAG: a ``repeat`` edge sends execution back to work that already ran, so
traversal terminates by bounding itself rather than by assuming acyclicity.

``test_the_seed_board_reports_exactly_its_documented_gaps`` is the floor. It
holds the seeded board to the gap list written in its own header, so "the
reviewer will catch these" is checked rather than claimed.
"""

import pytest
from pydantic import ValidationError

from meridian.domain.errors import IncompleteError, NotFoundError
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
    EntityConfig,
    EventConfig,
    Outcome,
)

PASS = Outcome(name="pass")
FAIL = Outcome(name="fail")


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
    assert "thing" not in {s.key for s in board.nodes()}
    assert [e.key for e in board.entities()] == ["thing"]


def test_an_entity_is_never_a_terminal_despite_having_no_edges(tiny: Board):
    board = tiny.model_copy(update={"primitives": (*tiny.primitives, EntityPrimitive(key="thing"))})
    assert "thing" not in {s.key for s in board.terminals()}


def test_entities_are_excluded_from_reachability(tiny: Board):
    board = tiny.model_copy(update={"primitives": (*tiny.primitives, EntityPrimitive(key="thing"))})
    assert "thing" not in board.reachable_from_events()


# --- lookup ----------------------------------------------------------------


def test_p_returns_the_card(tiny: Board):
    assert tiny.p("looks_ok").primitive_type == "check"


def test_p_raises_not_found_for_an_unknown_key(tiny: Board):
    with pytest.raises(NotFoundError):
        tiny.p("nope")


def test_events_actions_and_checks_partition_the_steps(tiny: Board):
    counted = len(tiny.events()) + len(tiny.actions()) + len(tiny.checks())
    assert counted == len(tiny.nodes())


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


# --- what a card declares --------------------------------------------------


def test_an_event_captures_what_it_both_reads_and_produces():
    event = EventPrimitive(
        key="arrived",
        config=EventConfig(name="Arrived", captures=["invoice"], outcomes=[PASS, FAIL]),
    )
    assert event.declared_outcomes() == (PASS, FAIL)
    assert event.declared_inputs() == ()
    assert event.reads() == ("invoice",)
    assert event.produces() == ("invoice",)


def test_an_action_reads_its_inputs_and_produces_what_its_lookup_names():
    action = ActionPrimitive(
        key="verify",
        config=ActionConfig(
            name="Verify",
            effect="lookup",
            inputs=["application"],
            produces="record",
            outcomes=[PASS],
        ),
    )
    assert action.declared_outcomes() == (PASS,)
    assert action.declared_inputs() == ("application",)
    assert action.reads() == ("application",)
    assert action.produces() == ("record",)


def test_an_action_that_looks_nothing_up_produces_nothing():
    assert ActionPrimitive(key="done", config=ActionConfig(name="Done")).produces() == ()


def test_a_check_reads_its_inputs_and_produces_nothing():
    check = CheckPrimitive(
        key="looks_ok",
        config=CheckConfig(name="Looks ok?", inputs=["invoice"], outcomes=[PASS, FAIL]),
    )
    assert check.declared_outcomes() == (PASS, FAIL)
    assert check.declared_inputs() == ("invoice",)
    assert check.reads() == ("invoice",)
    assert check.produces() == ()


def test_an_entity_declares_nothing_at_all():
    entity = EntityPrimitive(key="invoice", config=EntityConfig(name="Invoice"))
    assert entity.declared_outcomes() == ()
    assert entity.declared_inputs() == ()
    assert entity.reads() == ()
    assert entity.produces() == ()


# --- flow shape ------------------------------------------------------------


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


def test_dry_run_will_not_guess_the_only_conditional_edge(tiny: Board):
    # One edge left, and it carries 'pass'. A scenario silent about how the
    # check came out has not said the check passed.
    board = tiny.model_copy(update={"edges": tiny.edges[:2]})
    run = board.dry_run({})
    assert run.result == "undefined_branch"
    assert [s.key for s in run.trace] == ["arrived", "looks_ok"]
    assert "done" in run.unreached


def test_dry_run_takes_an_unconditional_edge_without_an_outcome(tiny: Board):
    run = tiny.dry_run({"looks_ok": "pass"})
    # 'arrived' names no outcome and is traversed anyway, because e1 is
    # unconditional and so depends on nothing the scenario withheld.
    assert [s.key for s in run.trace] == ["arrived", "looks_ok", "done"]
    assert run.trace[0].outcome is None


@pytest.fixture
def two_entries(tiny: Board) -> Board:
    """The same board, reachable from a second Event as well."""
    return tiny.model_copy(
        update={
            "primitives": (
                *tiny.primitives,
                EventPrimitive(key="chased", config=EventConfig(name="Chased")),
            ),
            "edges": (*tiny.edges, Edge(key="e4", from_key="chased", to_key="looks_ok")),
        }
    )


def test_dry_run_refuses_to_pick_between_two_entry_points(two_entries: Board):
    with pytest.raises(IncompleteError) as caught:
        two_entries.dry_run({"looks_ok": "pass"})
    assert "arrived" in str(caught.value)
    assert "chased" in str(caught.value)


def test_dry_run_walks_a_two_entry_board_from_the_start_it_is_given(two_entries: Board):
    run = two_entries.dry_run({"looks_ok": "pass"}, start="chased")
    assert run.result == "reached_terminal"
    assert [s.key for s in run.trace] == ["chased", "looks_ok", "done"]


# --- the seed board --------------------------------------------------------


def test_the_seed_board_loads_and_validates(seed: Board):
    assert len(seed.nodes()) == 6
    assert len(seed.entities()) == 2


def test_entities_carry_no_layout_position(seed: Board):
    # The whole implementation of "first-class but not drawn".
    assert not {e.key for e in seed.entities()} & set(seed.layout)


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
