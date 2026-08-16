"""Tests for the completeness contract.

Written one test per field rather than one per config, so a failure names the
field that broke instead of "the Action is wrong". Every Layer 1 lint rule reads
``findings()``, and the freeze gate is "no blocking findings", so this is where
"incomplete" is actually defined.

Two properties are easy to lose and asserted throughout: a half-filled card must
still be constructible, and a finished card must report nothing.
"""

import pytest
from pydantic import ValidationError

from meridian.domain.primitives import (
    ActionConfig,
    Cardinality,
    CheckConfig,
    Criterion,
    EntityConfig,
    EventConfig,
    EventRef,
    FieldRef,
    Finding,
    Operand,
    Outcome,
    RoleRef,
    Timing,
)


def fields(found: list[Finding]) -> set[str]:
    return {f.field for f in found}


def by_field(found: list[Finding]) -> dict[str, Finding]:
    return {f.field: f for f in found}


PASS_FAIL = [Outcome(name="pass"), Outcome(name="fail")]
INVOICE_NO = FieldRef(entity="commercial_invoice", path="invoice_no")
BATCH_NO = FieldRef(entity="commercial_invoice", path="line_items[].batch_no")


# --- field references ------------------------------------------------------


def test_field_ref_parses_a_nested_path():
    ref = FieldRef.parse("commercial_invoice.line_items[].hts_number")
    assert ref.entity == "commercial_invoice"
    assert ref.segments() == ["line_items", "hts_number"]


def test_field_ref_detects_an_array_hop():
    assert BATCH_NO.is_iterated()
    assert not INVOICE_NO.is_iterated()


def test_field_ref_rejects_an_empty_path():
    with pytest.raises(ValidationError):
        FieldRef(entity="commercial_invoice", path="")


def test_field_ref_round_trips_through_its_string_form():
    original = "commercial_invoice.line_items[].batch_no"
    assert str(FieldRef.parse(original)) == original


def test_field_ref_rejects_an_entity_that_is_not_a_board_key():
    with pytest.raises(ValidationError):
        FieldRef(entity="Commercial Invoice", path="invoice_no")


def test_role_and_event_recipients_discriminate_on_kind():
    role = RoleRef(role="receiving_supervisor")
    event = EventRef(source="prealert_received", field="sender")
    assert (role.kind, event.kind) == ("role", "event")


# --- cardinality -----------------------------------------------------------


def test_cardinality_defaults_to_one():
    assert Cardinality().kind == "one"
    assert Cardinality().findings() == []


def test_one_per_without_a_reference_is_blocking():
    assert by_field(Cardinality(kind="one_per").findings())["per"].severity == "blocking"


def test_one_per_names_the_field_that_enumerates_the_instances():
    # "one COA per batch on the invoice" is what makes the expected count
    # derivable rather than configured.
    cardinality = Cardinality(kind="one_per", per=BATCH_NO)
    assert cardinality.findings() == []
    assert str(cardinality.per) == "commercial_invoice.line_items[].batch_no"


# --- timing ----------------------------------------------------------------


def test_sla_without_a_deadline_is_blocking():
    found = Timing(kind="sla").findings()
    assert by_field(found)["deadline"].severity == "blocking"


def test_await_without_a_deadline_is_allowed():
    # Waiting indefinitely is a decision the reviewer questions as a shape, not
    # a field the process owner forgot.
    assert Timing(kind="await").findings() == []


def test_scheduled_mode_without_a_schedule_is_blocking():
    found = Timing(kind="await", mode="scheduled").findings()
    assert by_field(found)["schedule"].severity == "blocking"


@pytest.mark.parametrize("duration", ["PT48H", "P30D", "PT30S", "P1Y2M3DT4H5M6S"])
def test_valid_iso_8601_durations_are_accepted(duration: str):
    assert Timing(kind="sla", deadline=duration).deadline == duration


@pytest.mark.parametrize("duration", ["48H", "P", "", "PT", "2 days"])
def test_malformed_durations_are_rejected(duration: str):
    with pytest.raises(ValidationError):
        Timing(kind="sla", deadline=duration)


# --- event -----------------------------------------------------------------


def test_empty_event_reports_its_blocking_fields():
    assert {"name", "correlation_key", "timing"} <= fields(EventConfig().findings())


def test_event_with_a_deadline_and_no_outcomes_is_blocking():
    config = EventConfig(timing=Timing(kind="await", deadline="PT48H"))
    assert by_field(config.findings())["outcomes"].severity == "blocking"


def test_event_without_a_deadline_needs_no_outcomes():
    config = EventConfig(timing=Timing(kind="await"))
    assert "outcomes" not in fields(config.findings())


def test_an_event_can_never_be_terminal():
    with pytest.raises(ValidationError):
        EventConfig(is_terminal=True)


def test_event_timing_findings_are_reported_under_a_timing_prefix():
    config = EventConfig(timing=Timing(kind="sla"))
    assert "timing.deadline" in fields(config.findings())


def test_event_without_captures_is_important_not_blocking():
    assert by_field(EventConfig().findings())["captures"].severity == "important"


# --- action ----------------------------------------------------------------


def test_action_without_an_effect_is_blocking():
    assert by_field(ActionConfig().findings())["effect"].severity == "blocking"


def test_notify_without_recipients_is_blocking():
    found = ActionConfig(effect="notify", channel="email").findings()
    assert by_field(found)["recipients"].severity == "blocking"


def test_notify_without_a_channel_is_blocking():
    found = ActionConfig(effect="notify", recipients=[RoleRef(role="supervisor")]).findings()
    assert by_field(found)["channel"].severity == "blocking"


def test_notify_without_payload_fields_is_important():
    found = ActionConfig(
        effect="notify", channel="email", recipients=[RoleRef(role="supervisor")]
    ).findings()
    assert by_field(found)["payload_fields"].severity == "important"


def test_notify_without_an_idempotency_key_is_important():
    # A shipment resumes when corrected paperwork arrives and the check re-runs.
    # Without a dedupe key the supervisor is emailed about the same problem twice.
    found = ActionConfig(
        effect="notify", channel="email", recipients=[RoleRef(role="supervisor")]
    ).findings()
    assert by_field(found)["idempotency_key"].severity == "important"


def test_record_without_a_system_is_blocking():
    assert by_field(ActionConfig(effect="record").findings())["system"].severity == "blocking"


def test_record_without_an_idempotency_key_is_important():
    found = ActionConfig(effect="record", system="Aurologistics WMS").findings()
    assert by_field(found)["idempotency_key"].severity == "important"


def test_lookup_without_produces_is_blocking():
    found = ActionConfig(effect="lookup", system="state medical board").findings()
    assert by_field(found)["produces"].severity == "blocking"


def test_lookup_without_a_system_is_blocking():
    found = ActionConfig(effect="lookup", produces="board_record").findings()
    assert by_field(found)["system"].severity == "blocking"


def test_lookup_without_on_failure_is_important():
    found = ActionConfig(effect="lookup", system="state board", produces="board_record").findings()
    assert by_field(found)["on_failure"].severity == "important"


def test_a_lookup_produces_an_entity():
    # An Action that fetches something names the entity its answer lands in, so
    # a Check reads it exactly as it reads one that arrived by email.
    action = ActionConfig(
        effect="lookup",
        system="state medical board",
        produces="board_record",
        on_failure="fail",
        timeout="PT30S",
    )
    assert "produces" not in fields(action.findings())


def test_produces_must_be_a_board_key():
    with pytest.raises(ValidationError):
        ActionConfig(effect="lookup", produces="Board Record")


def test_decide_without_outcomes_is_blocking():
    assert by_field(ActionConfig(effect="decide").findings())["outcomes"].severity == "blocking"


def test_decide_with_one_outcome_is_still_blocking():
    found = ActionConfig(effect="decide", outcomes=[Outcome(name="approved")]).findings()
    assert by_field(found)["outcomes"].severity == "blocking"


def test_decide_without_a_deadline_is_important():
    found = ActionConfig(effect="decide", outcomes=PASS_FAIL).findings()
    assert by_field(found)["timing"].severity == "important"


def test_decide_with_a_deadline_needs_an_on_timeout_outcome():
    found = ActionConfig(
        effect="decide",
        outcomes=PASS_FAIL,
        timing=Timing(kind="await", deadline="P5D"),
    ).findings()
    assert by_field(found)["on_timeout"].severity == "important"


def test_a_terminal_noop_action_is_complete():
    config = ActionConfig(
        name="Documentation validated",
        effect="noop",
        is_terminal=True,
    )
    assert config.findings() == []


def test_a_channel_on_a_step_that_notifies_nobody_is_minor():
    found = ActionConfig(effect="record", system="WMS", channel="email").findings()
    assert by_field(found)["channel"].severity == "minor"


def test_action_config_has_no_capabilities_field():
    # A process owner never sees a tool. Capabilities are derived onto the
    # frozen spec at bind time, so the field cannot reach the canvas.
    with pytest.raises(ValidationError):
        ActionConfig(effect="notify", capabilities=["email.send"])


# --- check -----------------------------------------------------------------


def test_check_with_one_outcome_is_blocking():
    found = CheckConfig(outcomes=[Outcome(name="pass")]).findings()
    assert by_field(found)["outcomes"].severity == "blocking"


def test_check_without_criteria_is_blocking():
    assert by_field(CheckConfig().findings())["criteria"].severity == "blocking"


def test_check_without_inputs_is_blocking():
    assert by_field(CheckConfig().findings())["inputs"].severity == "blocking"


def test_check_without_on_missing_input_is_important():
    assert by_field(CheckConfig().findings())["on_missing_input"].severity == "important"


def test_check_without_evidence_is_minor():
    assert by_field(CheckConfig().findings())["evidence"].severity == "minor"


def test_check_inputs_are_entity_keys():
    config = CheckConfig(inputs=["application", "board_record"])
    assert config.inputs == ("application", "board_record")


def test_check_inputs_reject_something_that_is_not_a_board_key():
    with pytest.raises(ValidationError):
        CheckConfig(inputs=["Commercial Invoice"])


def test_per_line_item_scope_requires_a_criterion_that_reads_one():
    config = CheckConfig(
        scope="per_line_item",
        criteria=[Criterion(op="present", left=INVOICE_NO)],
        inputs=["commercial_invoice"],
        outcomes=PASS_FAIL,
        on_missing_input="wait",
        evidence=[INVOICE_NO],
    )
    assert by_field(config.findings())["scope"].severity == "important"


def test_per_line_item_scope_is_satisfied_by_an_iterated_reference():
    config = CheckConfig(
        scope="per_line_item",
        criteria=[Criterion(op="present", left=BATCH_NO)],
        inputs=["commercial_invoice"],
        outcomes=PASS_FAIL,
        on_missing_input="wait",
        evidence=[INVOICE_NO],
        name="Does every line item carry a batch number?",
    )
    assert config.findings() == []


def test_criterion_findings_are_reported_under_an_indexed_prefix():
    config = CheckConfig(
        criteria=[Criterion(op="present", left=INVOICE_NO), Criterion(op="compare")],
        inputs=["commercial_invoice"],
        outcomes=PASS_FAIL,
        on_missing_input="wait",
        evidence=[INVOICE_NO],
    )
    assert "criteria[1].operator" in fields(config.findings())


# --- criteria --------------------------------------------------------------


def test_a_criterion_without_a_field_is_blocking():
    assert by_field(Criterion(op="present").findings())["left"].severity == "blocking"


def test_compare_requires_an_operator():
    found = Criterion(op="compare", left=INVOICE_NO, right=Operand(kind="value", value=0.18))
    assert by_field(found.findings())["operator"].severity == "blocking"


def test_compare_requires_something_to_compare_against():
    found = Criterion(op="compare", left=INVOICE_NO, operator="gt").findings()
    assert by_field(found)["right"].severity == "blocking"


def test_a_value_operand_without_a_value_is_blocking():
    found = Criterion(
        op="compare", left=INVOICE_NO, operator="gt", right=Operand(kind="value")
    ).findings()
    assert by_field(found)["right.value"].severity == "blocking"


def test_compare_covers_pattern_matching():
    # `match` used to be its own operator; a regex test is a comparison whose
    # operator is `matches`.
    criterion = Criterion(
        op="compare",
        left=BATCH_NO,
        operator="matches",
        right=Operand(kind="value", value=r"^[A-Z]{3}\d{5}$"),
    )
    assert criterion.findings() == []


def test_compare_covers_expiry_against_the_current_time():
    # `temporal` used to be its own operator. "Has the licence expired" is a
    # comparison whose right-hand side is now.
    criterion = Criterion(
        op="compare",
        left=FieldRef(entity="licence", path="expires_on"),
        operator="gt",
        right=Operand(kind="now"),
    )
    assert criterion.findings() == []


def test_compare_covers_a_window_around_another_field():
    # "within 30 days of the order date" — a field operand with an offset.
    criterion = Criterion(
        op="compare",
        left=FieldRef(entity="return_request", path="received_on"),
        operator="lte",
        right=Operand(
            kind="field", field=FieldRef(entity="order", path="ordered_on"), offset="P30D"
        ),
    )
    assert criterion.findings() == []


def test_each_has_matching_requires_a_right_hand_reference():
    found = Criterion(op="each_has_matching", left=BATCH_NO).findings()
    assert by_field(found)["right"].severity == "blocking"


def test_each_has_matching_cannot_match_against_a_literal():
    found = Criterion(
        op="each_has_matching", left=BATCH_NO, right=Operand(kind="value", value="UAC25022")
    ).findings()
    assert by_field(found)["right"].severity == "blocking"


def test_present_requires_nothing_beyond_a_field():
    assert Criterion(op="present", left=INVOICE_NO).findings() == []


def test_custom_requires_a_statement():
    found = Criterion(op="custom", reads=[BATCH_NO]).findings()
    assert by_field(found)["statement"].severity == "blocking"


def test_custom_without_named_fields_is_important():
    # Lint cannot resolve anything a prose rule reads unless it is named, so the
    # escape hatch does not become a hole in reference checking.
    found = Criterion(op="custom", statement="batches match by product code").findings()
    assert by_field(found)["reads"].severity == "important"


def test_a_complete_custom_criterion_reports_nothing():
    criterion = Criterion(
        op="custom",
        statement="a batch matches its COA by product code, not by page order",
        reads=[BATCH_NO, FieldRef(entity="certificate_of_analysis", path="product_code")],
    )
    assert criterion.findings() == []


def test_references_covers_typed_and_custom_criteria_alike():
    typed = Criterion(
        op="each_has_matching",
        left=BATCH_NO,
        right=Operand(
            kind="field", field=FieldRef(entity="certificate_of_analysis", path="batch_no")
        ),
    )
    custom = Criterion(op="custom", statement="…", reads=[BATCH_NO, INVOICE_NO])
    assert len(typed.references()) == 2
    assert len(custom.references()) == 2


# --- entity ----------------------------------------------------------------


def test_empty_entity_reports_its_blocking_fields():
    assert {"name", "fields"} <= fields(EntityConfig().findings())


def test_a_card_says_nothing_about_how_it_is_recognised():
    # Only an entity that ARRIVES needs recognising, and a card cannot know how
    # it gets here — one a lookup returns, or one the checks fill in, has
    # nothing to recognise. The board-level rule owns this, so a finding here
    # would be one nobody could act on.
    found = EntityConfig(name="Board record", fields={"status": {}}).findings()
    assert "identified_by" not in fields(found)


def test_a_missing_sample_is_not_a_finding():
    # A process owner has no way to supply one — samples come from the corpus
    # via the fixture pull — so a finding here could never be cleared.
    found = EntityConfig(
        name="Commercial Invoice",
        identified_by="the page header reads 'Commercial Invoice'",
        fields={"invoice_no": {"type": "string"}},
    ).findings()
    assert found == []


def test_entity_cardinality_findings_are_reported_under_a_prefix():
    found = EntityConfig(cardinality=Cardinality(kind="one_per")).findings()
    assert "cardinality.per" in fields(found)


# --- properties every config shares ----------------------------------------


def test_a_typo_in_a_config_field_is_an_error():
    with pytest.raises(ValidationError):
        CheckConfig(quantifer="all")


def test_configs_are_immutable():
    config = ActionConfig(effect="noop")
    with pytest.raises(ValidationError):
        config.effect = "notify"


def test_sequence_fields_are_immutable():
    config = CheckConfig(outcomes=PASS_FAIL)
    with pytest.raises((AttributeError, TypeError)):
        config.outcomes.append(Outcome(name="third"))  # type: ignore[attr-defined]


def test_findings_are_stable_across_calls():
    config = ActionConfig(effect="notify")
    assert config.findings() == config.findings()


def test_a_half_filled_card_is_still_constructible():
    assert ActionConfig(effect="notify").findings() != []


# --- no-regression floor: a finished board is silent -----------------------


def test_completed_prealert_event_reports_nothing(prealert_received: EventConfig):
    assert prealert_received.findings() == []


def test_completed_prealert_entities_report_nothing(
    commercial_invoice: EntityConfig, certificate_of_analysis: EntityConfig
):
    assert commercial_invoice.findings() == []
    assert certificate_of_analysis.findings() == []


def test_completed_prealert_checks_report_nothing(
    invoice_complete: CheckConfig, coas_valid: CheckConfig
):
    assert invoice_complete.findings() == []
    assert coas_valid.findings() == []


def test_completed_prealert_actions_report_nothing(
    report_invoice_discrepancy: ActionConfig,
    report_coa_discrepancy: ActionConfig,
    documentation_validated: ActionConfig,
):
    assert report_invoice_discrepancy.findings() == []
    assert report_coa_discrepancy.findings() == []
    assert documentation_validated.findings() == []
