"""Tests for threads, assertions and the transitions between statuses.

The property that matters most: a process owner cannot declare a thread
resolved. `answered` means the knowledge exists; `resolved` means the board
reflects it, and only re-running the originating scenario establishes that. The
transition table is what stops the loop being decorative.
"""

import uuid

import pytest
from pydantic import ValidationError

from meridian.domain.review import (
    Anchor,
    Assertion,
    Evidence,
    Scenario,
    Thread,
    ThreadMessage,
)


def thread(**overrides: object) -> Thread:
    base: dict[str, object] = {
        "category": "missing_path",
        "question": "What happens when a COA is mismatched?",
    }
    return Thread(**(base | overrides))  # type: ignore[arg-type]


# --- anchors ---------------------------------------------------------------


def test_an_anchor_round_trips_through_its_string_form():
    assert str(Anchor.parse("primitive:coas_valid")) == "primitive:coas_valid"
    assert str(Anchor.parse("board")) == "board"


def test_a_board_anchor_carries_no_key():
    assert Anchor(kind="board").key is None


def test_a_board_anchor_with_a_key_is_rejected():
    with pytest.raises(ValidationError):
        Anchor(kind="board", key="something")


def test_a_non_board_anchor_without_a_key_is_rejected():
    with pytest.raises(ValidationError):
        Anchor(kind="primitive")


def test_an_entity_field_anchor_addresses_a_field():
    anchor = Anchor.parse("entity_field:commercial_invoice.batch_no")
    assert anchor.key == "commercial_invoice.batch_no"


# --- thread status ---------------------------------------------------------


def test_an_open_thread_can_be_answered_or_rejected():
    assert thread().may_become("answered")
    assert thread().may_become("rejected")


def test_an_open_thread_cannot_skip_straight_to_resolved():
    # Two loops, not one. `answered` settles the business knowledge; `resolved`
    # means the drawing now shows it, and those come apart constantly — knowing
    # "it comes back once corrected" and having drawn the repeat edge are
    # different states. Allowing this would collapse the revision loop.
    assert not thread().may_become("resolved")


def test_a_resolved_thread_reopens_if_its_scenario_fails_again():
    assert thread(status="resolved").may_become("open")


def test_a_thread_carries_why_it_is_being_asked():
    asked = thread(reason="The SOP ends at reporting and never says what closes the shipment.")
    assert asked.reason is not None


def test_an_answered_thread_can_resolve_or_fall_back_open():
    answered = thread(status="answered")
    assert answered.may_become("resolved")
    assert answered.may_become("open")


def test_a_rejected_thread_is_terminal():
    rejected = thread(status="rejected")
    assert not any(rejected.may_become(s) for s in ("open", "answered", "resolved"))


def test_a_resolved_thread_can_reopen_if_the_scenario_fails_again():
    assert thread(status="resolved").may_become("open")


def test_resolved_and_rejected_both_stop_blocking_a_freeze():
    assert thread(status="resolved").is_settled()
    assert thread(status="rejected").is_settled()
    assert not thread(status="answered").is_settled()


# --- thread shape ----------------------------------------------------------


def test_a_thread_may_span_several_elements():
    # A conversation ranges over a primitive, the edge leaving it and the card
    # it leads to. Its assertions will not.
    spanning = thread(
        anchors=[
            Anchor.parse("primitive:coas_valid"),
            Anchor.parse("edge:e5"),
            Anchor.parse("primitive:report_coa_discrepancy"),
        ]
    )
    assert len(spanning.anchors) == 3
    assert str(spanning.primary_anchor()) == "primitive:coas_valid"


def test_a_thread_with_no_anchors_has_no_primary():
    assert thread().primary_anchor() is None


def test_a_structural_claim_carries_the_evidence_that_produced_it():
    claim = thread(
        evidence=Evidence(
            tool="dry_run",
            result="dead_end",
            detail={"unreached": ["coas_valid"]},
        )
    )
    assert claim.evidence is not None
    assert claim.evidence.tool == "dry_run"


def test_messages_keep_their_order_and_author():
    conversation = thread(
        messages=[
            ThreadMessage(seq=1, author="ai", body="What happens on a mismatch?"),
            ThreadMessage(seq=2, author="human", body="It comes back once corrected."),
        ]
    )
    assert [m.author for m in conversation.messages] == ["ai", "human"]


# --- assertions ------------------------------------------------------------


def test_an_assertion_has_exactly_one_anchor():
    # A settled statement compiles into one file. Multi-anchored assertions
    # would inject the same sentence into three modules.
    settled = Assertion(
        anchor=Anchor.parse("primitive:coas_valid"),
        kind="exception",
        statement="Re-run every batch when a corrected COA arrives.",
    )
    assert settled.anchor.key == "coas_valid"


def test_a_constraint_without_something_machine_checkable_is_rejected():
    with pytest.raises(ValidationError):
        Assertion(
            anchor=Anchor.parse("entity_field:commercial_invoice.batch_no"),
            kind="constraint",
            statement="Batch numbers are seven digits.",
        )


def test_a_constraint_with_a_checkable_form_is_accepted():
    checkable = Assertion(
        anchor=Anchor.parse("entity_field:commercial_invoice.batch_no"),
        kind="constraint",
        statement="Batch numbers are seven digits.",
        constraint_json={"pattern": r"^\d{7}$"},
    )
    assert checkable.constraint_json is not None


def test_other_assertion_kinds_need_no_machine_form():
    assert (
        Assertion(
            anchor=Anchor(kind="board"),
            kind="rule",
            statement="One container is one shipment.",
        ).constraint_json
        is None
    )


def test_a_superseded_assertion_is_no_longer_active():
    later = uuid.uuid4()
    original = Assertion(
        anchor=Anchor(kind="board"),
        kind="rule",
        statement="One container is one shipment.",
        superseded_by=later,
    )
    assert not original.is_active()


# --- scenarios -------------------------------------------------------------


def test_a_scenario_is_runnable_rather_than_merely_described():
    # `outcomes` is exactly what Board.dry_run takes, so a thread can cite a
    # trace instead of an opinion.
    probe = Scenario(
        key="coa_mismatched",
        kind="probe",
        description="A COA arrives whose batch number differs by case.",
        outcomes={"invoice_complete": "pass", "coas_valid": "mismatched_coa"},
    )
    assert probe.outcomes["coas_valid"] == "mismatched_coa"
