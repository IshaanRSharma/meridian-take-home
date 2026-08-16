"""Tests for what counts as incomplete.

Every rule gets two tests: it fires on a board that violates it, and it is
silent on one that does not. The second half matters as much as the first — a
rule that fires on a finished board is noise, and noise is what makes a process
owner stop reading findings.

``test_the_seed_board_reports_exactly_its_documented_gaps`` is the floor for the
whole compiler. It holds the seeded board to the list written in its own header,
so "the reviewer will catch these" is checked rather than claimed.
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
    Cardinality,
    CheckConfig,
    Criterion,
    EntityConfig,
    EventConfig,
    FieldRef,
    Outcome,
    RoleRef,
)

PASS = Outcome(name="pass")
FAIL = Outcome(name="fail")


def anchors(board: Board) -> set[str]:
    return {f"{f.anchor}:{f.field}" for f in rules.findings(board)}


def fired(rule: rules.Rule, board: Board) -> set[str]:
    return {f"{f.anchor}:{f.field}" for f in rule(board)}


@pytest.fixture
def sound() -> Board:
    """A small board with nothing wrong with it."""
    return Board(
        name="sound",
        primitives=[
            EntityPrimitive(
                key="invoice",
                config=EntityConfig(
                    name="Invoice", identified_by="header reads Invoice", fields={"no": {}}
                ),
            ),
            EventPrimitive(
                key="arrived",
                config=EventConfig(
                    name="Arrived",
                    correlation_key=FieldRef(entity="invoice", path="no"),
                    match_condition="subject contains Invoice",
                    captures=["invoice"],
                    timing={"kind": "await"},
                ),
            ),
            CheckPrimitive(
                key="looks_ok",
                config=CheckConfig(
                    name="Looks ok?",
                    criteria=[Criterion(op="present", left=FieldRef(entity="invoice", path="no"))],
                    inputs=["invoice"],
                    outcomes=[PASS, FAIL],
                    evidence=[FieldRef(entity="invoice", path="no")],
                    on_missing_input="wait",
                ),
            ),
            ActionPrimitive(
                key="done", config=ActionConfig(name="Done", effect="noop", is_terminal=True)
            ),
            ActionPrimitive(
                key="complain",
                config=ActionConfig(
                    name="Complain",
                    effect="notify",
                    channel="email",
                    recipients=[RoleRef(role="supervisor")],
                    payload_fields=[FieldRef(entity="invoice", path="no")],
                    idempotency_key="no",
                    is_terminal=True,
                ),
            ),
        ],
        edges=[
            Edge(key="e1", from_key="arrived", to_key="looks_ok"),
            Edge(key="e2", from_key="looks_ok", to_key="done", on_outcomes=["pass"]),
            Edge(key="e3", from_key="looks_ok", to_key="complain", on_outcomes=["fail"]),
        ],
    )


def test_a_sound_board_reports_nothing(sound: Board):
    # The no-regression floor. Every rule below fires on something; none of them
    # may fire on this.
    assert rules.findings(sound) == []


# --- edges -----------------------------------------------------------------


def test_an_edge_to_a_card_that_is_not_there_is_a_finding(sound: Board):
    board = sound.model_copy(
        update={"edges": (*sound.edges, Edge(key="e9", from_key="done", to_key="ghost"))}
    )
    assert "edge:e9:to_key" in fired(rules.edge_endpoints_exist, board)


def test_an_edge_to_an_entity_is_a_finding(sound: Board):
    board = sound.model_copy(
        update={"edges": (*sound.edges, Edge(key="e9", from_key="done", to_key="invoice"))}
    )
    assert "edge:e9:to_key" in fired(rules.edge_endpoints_are_steps, board)


def test_an_edge_carrying_an_undeclared_outcome_is_a_finding(sound: Board):
    board = sound.model_copy(
        update={
            "edges": (
                sound.edges[0],
                Edge(key="e2", from_key="looks_ok", to_key="done", on_outcomes=["invented"]),
                sound.edges[2],
            )
        }
    )
    assert "edge:e2:on_outcomes" in fired(rules.edge_outcomes_are_declared, board)


def test_an_outcome_leading_nowhere_is_a_finding(sound: Board):
    check = CheckPrimitive(
        key="looks_ok",
        config=sound.p("looks_ok").config.model_copy(
            update={"outcomes": (PASS, FAIL, Outcome(name="odd"))}
        ),
    )
    board = sound.model_copy(
        update={
            "primitives": (sound.primitives[0], sound.primitives[1], check, *sound.primitives[3:])
        }
    )
    assert "primitive:looks_ok:outcomes" in fired(rules.outcomes_are_wired, board)


# --- entities and references -----------------------------------------------


def test_an_entity_nobody_reads_is_a_finding(sound: Board):
    spare = EntityPrimitive(
        key="spare", config=EntityConfig(name="Spare", identified_by="x", fields={"a": {}})
    )
    board = sound.model_copy(update={"primitives": (*sound.primitives, spare)})
    assert "primitive:spare:name" in fired(rules.entities_are_read, board)


def test_reading_an_entity_that_is_not_on_the_board_is_a_finding(sound: Board):
    check = CheckPrimitive(
        key="looks_ok",
        config=sound.p("looks_ok").config.model_copy(update={"inputs": ("invoice", "ghost")}),
    )
    board = sound.model_copy(
        update={
            "primitives": (sound.primitives[0], sound.primitives[1], check, *sound.primitives[3:])
        }
    )
    assert "primitive:looks_ok:inputs" in fired(rules.inputs_exist, board)


def test_reading_a_field_the_entity_does_not_carry_is_a_finding(sound: Board):
    check = CheckPrimitive(
        key="looks_ok",
        config=sound.p("looks_ok").config.model_copy(
            update={
                "criteria": (Criterion(op="present", left=FieldRef(entity="invoice", path="nope")),)
            }
        ),
    )
    board = sound.model_copy(
        update={
            "primitives": (sound.primitives[0], sound.primitives[1], check, *sound.primitives[3:])
        }
    )
    assert "primitive:looks_ok:criteria" in fired(rules.field_references_resolve, board)


def test_counting_against_a_field_that_does_not_exist_is_a_finding(sound: Board):
    # "One COA per batch on the invoice" is the only thing that knows how many
    # to expect, so a `per` that does not resolve is not a detail.
    coa = EntityPrimitive(
        key="coa",
        config=EntityConfig(
            name="COA",
            identified_by="header",
            fields={"batch_no": {}},
            cardinality=Cardinality(
                kind="one_per", per=FieldRef(entity="invoice", path="batches[]")
            ),
        ),
    )
    check = CheckPrimitive(
        key="looks_ok",
        config=sound.p("looks_ok").config.model_copy(update={"inputs": ("invoice", "coa")}),
    )
    board = sound.model_copy(
        update={
            "primitives": (
                sound.primitives[0],
                sound.primitives[1],
                check,
                *sound.primitives[3:],
                coa,
            )
        }
    )
    assert "primitive:coa:cardinality.per" in fired(rules.cardinality_resolves, board)


def test_an_arriving_entity_with_no_recognition_rule_is_blocking(sound: Board):
    entity = EntityPrimitive(key="invoice", config=EntityConfig(name="Invoice", fields={"no": {}}))
    board = sound.model_copy(update={"primitives": (entity, *sound.primitives[1:])})
    assert "primitive:invoice:identified_by" in fired(rules.entities_are_recognisable, board)


def test_an_entity_a_lookup_produces_needs_no_recognition_rule():
    # Nothing arrives to be recognised — the answer is whatever came back.
    board = Board(
        name="b",
        primitives=[
            EntityPrimitive(
                key="record", config=EntityConfig(name="Record", fields={"status": {}})
            ),
            ActionPrimitive(
                key="verify",
                config=ActionConfig(
                    name="Verify", effect="lookup", system="board", produces="record"
                ),
            ),
            CheckPrimitive(key="ck", config=CheckConfig(name="Ck", inputs=["record"])),
        ],
    )
    assert fired(rules.entities_are_recognisable, board) == set()


# --- the shape of the flow -------------------------------------------------


def test_an_event_with_nothing_after_it_is_a_finding():
    board = Board(name="b", primitives=[EventPrimitive(key="arrived")])
    assert "primitive:arrived:outgoing" in fired(rules.events_lead_somewhere, board)


def test_a_dead_end_that_is_not_marked_as_an_ending_is_a_finding(sound: Board):
    loose = ActionPrimitive(key="complain", config=ActionConfig(name="Complain", effect="noop"))
    board = sound.model_copy(update={"primitives": (*sound.primitives[:4], loose)})
    assert "primitive:complain:outgoing" in fired(rules.dead_ends_are_endings, board)


def test_a_card_nothing_leads_to_is_a_finding(sound: Board):
    orphan = ActionPrimitive(
        key="orphan", config=ActionConfig(name="Orphan", effect="noop", is_terminal=True)
    )
    board = sound.model_copy(update={"primitives": (*sound.primitives, orphan)})
    assert "primitive:orphan:incoming" in fired(rules.steps_are_reachable, board)


# --- merging ---------------------------------------------------------------


def test_one_problem_is_reported_once_at_its_worst_severity(sound: Board):
    # The card says its recognition rule is merely important; the board says it
    # is blocking, because nothing produces that entity. The process owner
    # should see one blank, at the severity that gates the freeze.
    entity = EntityPrimitive(key="invoice", config=EntityConfig(name="Invoice", fields={"no": {}}))
    board = sound.model_copy(update={"primitives": (entity, *sound.primitives[1:])})

    matching = [f for f in rules.findings(board) if f.field == "identified_by"]
    assert len(matching) == 1
    assert matching[0].severity == "blocking"


def test_findings_come_back_worst_first(sound: Board):
    board = sound.model_copy(
        update={"edges": (*sound.edges, Edge(key="e9", from_key="done", to_key="ghost"))}
    )
    severities = [f.severity for f in rules.findings(board)]
    assert severities == sorted(severities, key=lambda s: -rules.SEVERITY_RANK[s])


def test_a_finding_says_where_its_fix_lives(sound: Board):
    # The two surfaces a process owner works on. `field` findings become blanks
    # in the inspector; `structure` findings are canvas gestures — draw the
    # missing line, mark the card as an ending.
    orphan = ActionPrimitive(
        key="orphan", config=ActionConfig(name="Orphan", effect="noop", is_terminal=True)
    )
    entity = EntityPrimitive(key="invoice", config=EntityConfig(name="Invoice", fields={"no": {}}))
    board = sound.model_copy(update={"primitives": (entity, *sound.primitives[1:], orphan)})

    by_anchor = {f"{f.anchor}:{f.field}": f.kind for f in rules.findings(board)}
    assert by_anchor["primitive:orphan:incoming"] == "structure"
    assert by_anchor["primitive:invoice:identified_by"] == "field"


def test_every_structural_finding_names_something_that_is_not_a_config_field(sound: Board):
    # If a `structure` finding pointed at a real attribute, the inspector would
    # render a blank for something no blank can fix.
    board = sound.model_copy(
        update={"edges": (*sound.edges, Edge(key="e9", from_key="done", to_key="ghost"))}
    )
    for finding in rules.findings(board):
        if finding.kind == "structure":
            assert finding.field in {
                "outgoing",
                "incoming",
                "inputs",
                "outcomes",
                "name",
                "from_key",
                "to_key",
                "on_outcomes",
            }


def test_tool_resolution_is_not_part_of_this_gate(sound: Board):
    # A process owner cannot fix an unbound channel, so putting it on the canvas
    # would be noise they can do nothing about. Different gate, different owner.
    assert not any("capabilit" in f.field for f in rules.findings(sound))


# --- the seed board --------------------------------------------------------


def test_the_seed_board_reports_exactly_its_documented_gaps(seed: Board):
    assert {f"{f.anchor}:{f.field}" for f in rules.blocking(seed)} == {
        "primitive:report_coa_discrepancy:recipients",  # the SOP names nobody
        "primitive:report_invoice_discrepancy:system",  # "log an error" — where?
        "primitive:coas_valid:outcomes",  # mismatched_coa goes nowhere
    }


def test_the_seed_boards_softer_gaps_do_not_block_a_freeze(seed: Board):
    important = {f"{f.anchor}:{f.field}" for f in rules.findings(seed) if f.severity == "important"}
    assert {
        "primitive:invoice_complete:on_missing_input",
        "primitive:coas_valid:on_missing_input",
        "primitive:report_coa_discrepancy:idempotency_key",
        "primitive:report_invoice_discrepancy:idempotency_key",
    } <= important
