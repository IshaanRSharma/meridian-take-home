"""Tests for what counts as incomplete.

Every rule gets two tests: it fires on a board that violates it, and it is
silent on one that does not. The second half matters as much as the first — a
rule that fires on a finished board is noise, and noise is what makes a process
owner stop reading findings.

``test_the_seed_board_reports_exactly_its_documented_gaps`` is the floor for the
whole compiler. It holds the seeded board to the list written in its own header,
so "the reviewer will catch these" is checked rather than claimed.
"""

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
    FieldRef,
    Fill,
    Outcome,
)

SUMMARY = EntityPrimitive(
    key="shipment_summary",
    config=EntityConfig(
        name="Shipment summary",
        fields={"checked": {}, "failed": {}, "status": {}},
    ),
)

PASS = Outcome(name="pass")
FAIL = Outcome(name="fail")


def anchors(board: Board) -> set[str]:
    return {f"{f.anchor}:{f.field}" for f in rules.findings(board)}


def fired(rule: rules.Rule, board: Board) -> set[str]:
    return {f"{f.anchor}:{f.field}" for f in rule(board)}


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


def test_a_card_reading_a_field_from_something_it_was_never_given_is_a_finding(sound: Board):
    # Every other reference rule asks whether the field exists. This one asks
    # whether the step was handed the card it reads from — a step can name a
    # real field on a real entity and still never receive it, which reaches
    # codegen as an email quoting an invoice number nobody passed in.
    complain = ActionPrimitive(
        key="complain", config=sound.p("complain").config.model_copy(update={"inputs": ()})
    )
    board = sound.model_copy(update={"primitives": (*sound.primitives[:4], complain)})
    assert "primitive:complain:inputs" in fired(rules.references_are_declared, board)


def test_an_event_may_reference_what_it_captures(sound: Board):
    # An Event declares no inputs at all — what it captures is what it reads. A
    # rule that only looked at `inputs` would fire on every correlation key.
    assert "primitive:arrived:correlation_key" not in fired(rules.references_are_declared, sound)


def test_a_lookup_may_reference_what_it_brings_back():
    board = Board(
        name="b",
        primitives=[
            EntityPrimitive(
                key="record", config=EntityConfig(name="Record", fields={"status": {}})
            ),
            ActionPrimitive(
                key="verify",
                config=ActionConfig(
                    name="Verify",
                    effect="lookup",
                    system="board",
                    produces="record",
                    payload_fields=[FieldRef(entity="record", path="status")],
                ),
            ),
        ],
    )
    assert fired(rules.references_are_declared, board) == set()


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


def test_one_problem_is_reported_once(sound: Board):
    # A check with a single outcome that also leads nowhere trips two rules on
    # the same (anchor, field). The process owner should see one entry.
    check = CheckPrimitive(
        key="looks_ok",
        config=sound.p("looks_ok").config.model_copy(update={"outcomes": (Outcome(name="odd"),)}),
    )
    board = sound.model_copy(
        update={
            "primitives": (sound.primitives[0], sound.primitives[1], check, *sound.primitives[3:])
        }
    )
    matching = [
        f
        for f in rules.findings(board)
        if f.anchor == "primitive:looks_ok" and f.field == "outcomes"
    ]
    assert len(matching) == 1


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


def _with_summary(sound: Board, *fills: Fill) -> Board:
    """The sound board, plus a produced summary the check writes into."""
    check = CheckPrimitive(
        key="looks_ok", config=sound.p("looks_ok").config.model_copy(update={"fills": fills})
    )
    report = ActionPrimitive(
        key="complain",
        config=sound.p("complain").config.model_copy(
            update={"inputs": ("invoice", "shipment_summary")}
        ),
    )
    return sound.model_copy(
        update={
            "primitives": (
                sound.primitives[0],
                sound.primitives[1],
                check,
                sound.primitives[3],
                report,
                SUMMARY,
            )
        }
    )


# --- what a check produces -------------------------------------------------


def test_a_summary_needs_no_recognition_rule_and_no_cardinality(sound: Board):
    # It never arrives, so there is nothing to recognise, and nothing counts it.
    board = _with_summary(
        sound,
        Fill(measure="checked", field=FieldRef(entity="shipment_summary", path="checked")),
        Fill(measure="failed", field=FieldRef(entity="shipment_summary", path="failed")),
        Fill(measure="passed", field=FieldRef(entity="shipment_summary", path="status")),
    )
    for finding in rules.findings(board):
        assert finding.anchor != "primitive:shipment_summary" or finding.field.startswith("fields")


def test_every_unfilled_column_is_reported_separately(sound: Board):
    """Two blank columns are two problems, not one.

    `findings` merges on (anchor, field) so that several rules describing one
    blank collapse into a single line. An unfilled column has to carry its own
    name into that key, or the second one is dropped — and dropped is worse than
    unreported, because the first is fixed and the card then looks finished.
    """
    board = _with_summary(
        sound,
        Fill(measure="checked", field=FieldRef(entity="shipment_summary", path="checked")),
    )

    reported = {f.field for f in rules.findings(board) if f.anchor == "primitive:shipment_summary"}
    assert reported == {"fields.failed", "fields.status"}


def test_a_summary_nothing_reads_is_not_a_finding(sound: Board):
    """The row a process produces is consumed outside it, so nobody on the board reads it.

    Distinct from the unread-entity rule it would otherwise trip: an invoice
    nothing looks at is a card somebody forgot to wire up, while a summary
    nothing looks at is the normal and only shape an output takes.
    """
    filled = CheckPrimitive(
        key="looks_ok",
        config=sound.p("looks_ok").config.model_copy(
            update={
                "fills": (
                    Fill(
                        measure="checked", field=FieldRef(entity="shipment_summary", path="checked")
                    ),
                )
            }
        ),
    )
    board = sound.model_copy(
        update={
            "primitives": (
                sound.primitives[0],
                sound.primitives[1],
                filled,
                *sound.primitives[3:],
                SUMMARY,
            )
        }
    )

    assert "primitive:shipment_summary:name" not in fired(rules.entities_are_read, board)


def test_an_entity_that_is_neither_read_nor_filled_is_still_a_finding(sound: Board):
    """The exemption is for being filled, not for being an entity."""
    board = sound.model_copy(update={"primitives": (*sound.primitives, SUMMARY)})

    assert "primitive:shipment_summary:name" in fired(rules.entities_are_read, board)


def test_filling_a_field_the_entity_does_not_have_is_blocking(sound: Board):
    board = _with_summary(
        sound,
        Fill(measure="checked", field=FieldRef(entity="shipment_summary", path="chekced")),
    )
    found = {f.field: f for f in rules.fills_resolve(board)}
    assert found["fills"].severity == "blocking"
    assert "chekced" in found["fills"].reason


def test_writing_into_something_that_arrives_is_blocking(sound: Board):
    # The invoice comes from outside. Writing into it is always a mistake.
    board = _with_summary(
        sound, Fill(measure="checked", field=FieldRef(entity="invoice", path="no"))
    )
    assert "primitive:looks_ok:fills" in fired(rules.fills_resolve, board)


def test_a_produced_field_nothing_fills_is_important(sound: Board):
    # The payoff: declare the columns and the ones with no check behind them
    # become findings on the card. This is the ASN gap, made visible.
    board = _with_summary(
        sound,
        Fill(measure="checked", field=FieldRef(entity="shipment_summary", path="checked")),
    )
    unfilled = rules.produced_fields_are_filled(board)
    assert {f.severity for f in unfilled} == {"important"}
    assert "'failed'" in " ".join(f.reason for f in unfilled)
    assert "'status'" in " ".join(f.reason for f in unfilled)


def test_one_rule_can_report_at_two_grains(sound: Board):
    # "Every line item carries four codes" produces both two failing line items
    # and one failing invoice. Same rule, two columns, one check.
    check = CheckPrimitive(
        key="looks_ok",
        config=sound.p("looks_ok").config.model_copy(
            update={
                "scope": "per_line_item",
                "fills": (
                    Fill(
                        measure="failed",
                        field=FieldRef(entity="shipment_summary", path="failed"),
                        per="per_line_item",
                    ),
                    Fill(
                        measure="failed",
                        field=FieldRef(entity="shipment_summary", path="checked"),
                        per="per_document",
                    ),
                ),
            }
        ),
    )
    assert [f for f in check.config.findings() if f.field == "fills"] == []


def test_an_edge_into_a_produced_entity_is_still_rejected(sound: Board):
    board = _with_summary(sound)
    board = board.model_copy(
        update={"edges": (*board.edges, Edge(key="e9", from_key="done", to_key="shipment_summary"))}
    )
    assert "edge:e9:to_key" in fired(rules.edge_endpoints_are_steps, board)


def test_tool_resolution_is_not_part_of_this_gate(sound: Board):
    # A process owner cannot fix an unbound channel, so putting it on the canvas
    # would be noise they can do nothing about. Different gate, different owner.
    assert not any("capabilit" in f.field for f in rules.findings(sound))


# --- the seed board --------------------------------------------------------


def test_the_seed_board_reports_exactly_its_documented_gaps(seed: Board):
    # All three are the drawing being an invalid workflow, which is the only
    # thing allowed to block. The SOP's real silences — who receives the report,
    # where the log goes — are `important`, because nobody but the process owner
    # can answer them and that is a conversation, not a refusal.
    assert {f"{f.anchor}:{f.field}" for f in rules.blocking(seed)} == {
        "primitive:coas_valid:outcomes",  # mismatched_coa goes nowhere
        "primitive:report_coa_discrepancy:outgoing",  # stops here, never says so
        "primitive:report_invoice_discrepancy:outgoing",  # same
    }


def test_the_seed_boards_softer_gaps_do_not_block_a_freeze(seed: Board):
    important = {f"{f.anchor}:{f.field}" for f in rules.findings(seed) if f.severity == "important"}
    assert {
        "primitive:invoice_complete:on_missing_input",
        "primitive:coas_valid:on_missing_input",
        "primitive:report_coa_discrepancy:idempotency_key",
        "primitive:report_invoice_discrepancy:idempotency_key",
        "primitive:report_coa_discrepancy:recipients",
        "primitive:report_invoice_discrepancy:system",
    } <= important


def test_the_thing_that_arrives_is_itself_modelled(seed: Board):
    # The email is not just a filter on a mailbox — it is parsed. Its sender is
    # who a report replies to, and its attachments are what get classified.
    # Left as a match_condition string, none of that is addressable, and
    # "whoever sent the pre-alert" cannot be expressed as a recipient at all.
    email = seed.p("prealert_email")
    assert email.key in seed.p("prealert_received").config.captures
    assert "sender" in email.config.fields


def test_every_rule_written_is_a_rule_that_runs():
    # A rule missing from RULES is dead code that still passes its own test, and
    # the failure is silent — findings() simply stops reporting that class. An
    # earlier draft kept a registry.py so that "complete" was readable; a tuple
    # does the same job only if something holds it against the module.
    written = {
        name
        for name, value in vars(rules).items()
        if callable(value)
        and not name.startswith("_")
        and getattr(value, "__module__", None) == rules.__name__
        and name not in {"findings", "blocking"}
    }
    assert written == {rule.__name__ for rule in rules.RULES}


def test_a_check_is_never_told_to_mark_itself_an_ending(sound: Board):
    # `is_terminal` exists on ActionConfig alone, so this finding on a Check
    # names a field that cannot be set — unclearable, and blocking, which is the
    # one combination that traps someone on the canvas with no move. A Check
    # that stops has unwired outcomes and `outcomes_are_wired` already says so,
    # in a sentence with something to do about it.
    board = sound.model_copy(update={"edges": (sound.edges[0],)})
    assert "primitive:looks_ok:outgoing" not in fired(rules.dead_ends_are_endings, board)
    assert "primitive:looks_ok:outcomes" in fired(rules.outcomes_are_wired, board)


def test_a_process_with_no_way_in_says_so_once():
    # Reachability is measured from the events, so a board with none reports
    # every step as unreachable — one problem wearing N blocking findings, none
    # of which names the actual cause.
    board = Board(
        name="batch",
        primitives=[
            ActionPrimitive(
                key="sweep",
                config=ActionConfig(
                    name="Sweep",
                    effect="record",
                    system="wms",
                    idempotency_key="d",
                    is_terminal=True,
                ),
            )
        ],
    )
    assert fired(rules.steps_are_reachable, board) == set()
    assert "board:events" in fired(rules.something_starts_the_process, board)


def test_a_step_cannot_produce_something_the_board_does_not_have(sound: Board):
    # The mirror of `inputs_exist`, and it matters most for a lookup — the third
    # direction data moves. The whole reason a lookup is one card rather than
    # three is that its answer becomes a thing other steps can read; naming an
    # entity nobody drew makes that answer unaddressable, so no Check could list
    # it in `inputs` and a generator writes a live API result into nowhere.
    looking_up = ActionPrimitive(
        key="verify_licence",
        config=ActionConfig(
            name="Verify the licence",
            effect="lookup",
            system="state medical board",
            produces="board_record",
            on_failure="wait",
        ),
    )
    board = sound.model_copy(update={"primitives": (*sound.primitives, looking_up)})

    assert "primitive:verify_licence:produces" in fired(rules.produced_entities_exist, board)


def test_a_step_producing_something_that_is_there_is_fine(sound: Board):
    assert fired(rules.produced_entities_exist, sound) == set()


# --- a step reading something nothing ever creates ---------------------------


def test_a_step_reading_an_entity_nothing_produces_is_reported(sound: Board):
    # The mirror of `produced_entities_exist`, and the direction nothing covered.
    # Every other rule passes it — the card is on the board, a step reads it, it
    # is declared in `inputs` — and `inputs_arrive_before_they_are_read` asks
    # whether the producer runs early enough, which is vacuously true when there
    # is no producer. So the board linted exactly as clean as one without it, and
    # codegen got a Check whose inputs name something that never exists.
    check = sound.checks()[0]
    inputs = (*check.config.inputs, "shipment_summary")
    config = check.config.model_copy(update={"inputs": inputs})
    reading = check.model_copy(update={"config": config})
    board = sound.model_copy(
        update={
            "primitives": (
                *(reading if card.key == check.key else card for card in sound.primitives),
                SUMMARY,
            )
        }
    )

    fired_on = fired(rules.something_produces_what_a_step_reads, board)
    assert f"primitive:{check.key}:inputs" in fired_on


def test_a_step_reading_something_the_process_creates_is_fine(sound: Board):
    assert fired(rules.something_produces_what_a_step_reads, sound) == set()


# --- several ways out of one step --------------------------------------------


def test_two_ways_out_with_nothing_to_choose_between_them_is_reported(sound: Board):
    # This vocabulary is sequential, and two unconditional edges out of one step
    # is how a process owner draws "these both happen, in no particular order".
    # Nothing here can express it, so it is said on the card rather than accepted
    # and then half-walked: before this rule such a board linted clean, reported
    # every step reachable, and dry-ran down whichever branch was drawn first.
    event = sound.events()[0]
    target = sound.checks()[0]
    board = sound.model_copy(
        update={
            "edges": (
                *sound.edges,
                Edge(key="parallel", from_key=event.key, to_key=target.key),
            )
        }
    )

    assert f"primitive:{event.key}:outgoing" in fired(rules.one_way_out_of_every_step, board)


def test_one_unconditional_way_out_is_how_every_board_starts(sound: Board):
    assert fired(rules.one_way_out_of_every_step, sound) == set()
