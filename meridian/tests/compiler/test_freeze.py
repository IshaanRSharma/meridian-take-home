"""Tests for the gate.

The freeze is where authority transfers from a person to a test suite, so the
tests that matter here are about what it *refuses* and how legibly. A refusal
carrying a count is useless — someone has to be told which three things nobody
wrote down — and that is the difference between a gate and a printout.

The other property is that a version means something. Two freezes of an
unchanged board are the same spec, and a checksum that moved because a counter
moved could not say so.
"""

import pytest

from meridian.compiler import freeze as f
from meridian.domain.errors import ConflictingStateError, IncompleteError
from meridian.domain.graph import Board, CheckPrimitive
from meridian.domain.review import Anchor, Assertion, Thread

OPEN = Thread(
    category="undefined_exception",
    question="Does a COA problem end the process?",
    status="open",
    anchors=(Anchor.parse("primitive:coas_valid"),),
)

SETTLED = (
    Assertion(
        anchor=Anchor(kind="board"),
        kind="rule",
        statement="one container is one shipment",
    ),
)


# --- the gate --------------------------------------------------------------


def test_the_seed_board_is_refused_and_told_what_is_missing(seed: Board):
    # The checkpoint. `spec freeze` refuses, and names three places the drawing
    # is not yet a workable process — an outcome with no line, and two steps it
    # stops at without saying so.
    with pytest.raises(f.BoardNotReadyError) as raised:
        f.freeze(seed, (), ())

    assert {(x.anchor, x.field) for x in raised.value.findings} == {
        ("primitive:coas_valid", "outcomes"),
        ("primitive:report_coa_discrepancy", "outgoing"),
        ("primitive:report_invoice_discrepancy", "outgoing"),
    }


def test_a_refusal_carries_sentences_a_person_can_act_on(seed: Board):
    # A count tells someone they failed; it does not tell them what to do. The
    # CLI prints these verbatim, so they have to be the reason strings.
    with pytest.raises(f.BoardNotReadyError) as raised:
        f.freeze(seed, (), ())

    assert "Nothing says what happens on 'mismatched_coa'." in {
        x.reason for x in raised.value.findings
    }


def test_a_refusal_names_only_what_actually_blocks(seed: Board):
    # The seed board has six softer findings. Listing them beside the three that
    # stop the freeze is how a gate turns back into a printout.
    with pytest.raises(f.BoardNotReadyError) as raised:
        f.freeze(seed, (), ())

    assert all(x.severity == "blocking" for x in raised.value.findings)


def test_the_refusal_is_the_one_the_api_already_maps(seed: Board):
    # Incomplete → 422 with findings is wired at the boundary. A new base class
    # here would need its own handler and would eventually not get one.
    with pytest.raises(IncompleteError):
        f.freeze(seed, (), ())


def test_a_sound_board_freezes(sound: Board):
    spec = f.freeze(sound, SETTLED, ())
    assert spec.version == 1
    assert spec.is_intact()
    assert set(spec.primitives) == {"arrived", "looks_ok", "done", "complain"}


# --- questions still open --------------------------------------------------


def test_an_unanswered_question_stops_the_freeze(sound: Board):
    with pytest.raises(f.BoardNotReadyError) as raised:
        f.freeze(sound, (), (OPEN,))

    assert raised.value.findings == []
    assert [t.question for t in raised.value.unsettled] == [OPEN.question]


def test_knowing_the_answer_is_not_enough(sound: Board):
    # `answered` means the rule is known; `resolved` means the drawing shows it.
    # The spec is built from the drawing, so freezing at `answered` would ship a
    # board that does not contain the answer someone just gave.
    with pytest.raises(f.BoardNotReadyError):
        f.freeze(sound, (), (OPEN.model_copy(update={"status": "answered"}),))


def test_a_question_that_was_dismissed_does_not_block(sound: Board):
    # `rejected` is not a delete — it is considered and set aside, and it still
    # crosses the freeze as negative knowledge.
    assert f.freeze(sound, (), (OPEN.model_copy(update={"status": "rejected"}),))
    assert f.freeze(sound, (), (OPEN.model_copy(update={"status": "resolved"}),))


# --- what a version means --------------------------------------------------


def test_freezing_an_unchanged_board_again_is_refused(sound: Board):
    # Otherwise a version number counts button presses rather than revisions,
    # and "which spec is build 4 against" stops being answerable.
    first = f.freeze(sound, (), ())
    with pytest.raises(ConflictingStateError):
        f.freeze(sound, (), (), previous=first)


def test_a_changed_board_becomes_the_next_version(sound: Board):
    first = f.freeze(sound, (), ())
    edited = sound.model_copy(
        update={
            "primitives": tuple(
                CheckPrimitive(
                    key=p.key, config=p.config.model_copy(update={"on_missing_input": "fail"})
                )
                if p.key == "looks_ok"
                else p
                for p in sound.primitives
            )
        }
    )

    second = f.freeze(edited, (), (), previous=first)
    assert second.version == 2
    assert second.checksum != first.checksum


def test_a_version_is_not_part_of_what_is_sealed(sound: Board):
    # The checksum answers "is this the same spec", which conformance and
    # `is_intact` both rely on. A version answers "which submission is this".
    # Mixing them makes two freezes of identical content disagree.
    first = f.freeze(sound, (), ())
    assert first.model_copy(update={"version": 9}).is_intact()


def test_settled_statements_reach_the_spec(sound: Board):
    spec = f.freeze(sound, SETTLED, ())
    assert "[rule] one container is one shipment" in spec.primitives["looks_ok"].context.inherited


def test_a_board_cannot_be_frozen_without_being_handed_its_conversation(sound: Board):
    # The gate that nearly was not one. With `assertions` and `threads`
    # defaulting to empty, a caller that forgot them froze a board with every
    # question still open and every scoped context empty — and the checksum
    # covered the content that was there, so nothing disagreed.
    with pytest.raises(TypeError):
        f.freeze(sound)  # type: ignore[call-arg]
