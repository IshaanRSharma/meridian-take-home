"""The contract between a generated agent and the sweep.

One property carries this file: **a step that failed must survive the crossing**.
The trace exists so a failure bundle can be assembled from it, and every part of
that is lost if the only thing reaching the harness is a list of step names — a
trace of successes omits exactly the line somebody needed to read.

The second property is that the crossing is *validated*. Rule 4 says keep the
trace's shape, and a rule nothing checks is a comment.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from meridian.runtime.harness import CaseOutcome
from meridian.runtime.trace import RunTrace

EVAL_ROW = {"coa_total": 5, "coa_success": 3, "failed_coa": 2, "status": "ACTIVE"}


def _unmatched_batches(trace: RunTrace) -> None:
    """A step that reaches out and then fails. The trace must keep both."""
    with trace.step("coas_valid") as step:
        step.tool_called("email.send", {"to": "role:supervisor"}, shadowed=True)
        raise RuntimeError("2 unmatched: ['UAC25022 ', 'uac25019']")


def traced() -> RunTrace:
    """A trace with one step that worked, one that did not, and a decline."""
    trace = RunTrace(spec_version=2, spec_checksum="c37d1f47")

    with trace.step("extract") as step:
        step.produced({"commercial_invoice": 1, "certificate_of_analysis": 3})
    with pytest.raises(RuntimeError):
        _unmatched_batches(trace)

    trace.decline("image001.gif", "matches no recognition rule")
    return trace


def test_a_failed_step_survives_the_crossing():
    outcome = CaseOutcome.from_dump(EVAL_ROW, traced().dump())

    failed = next(step for step in outcome.steps if step.status == "failed")
    assert failed.name == "coas_valid"
    # The message, not just the fact. `['UAC25022 ', 'uac25019']` shows trailing
    # whitespace and case variance, and that IS the diagnosis.
    assert "UAC25022 " in (failed.error or "")


def test_what_was_declined_survives_too():
    # Half of "found nothing" is a document that was skipped rather than absent,
    # and those have completely different fixes.
    outcome = CaseOutcome.from_dump(EVAL_ROW, traced().dump())

    assert [(d.source, d.reason) for d in outcome.declined] == [
        ("image001.gif", "matches no recognition rule")
    ]


def test_a_tool_call_stays_attributed_to_its_step():
    outcome = CaseOutcome.from_dump(EVAL_ROW, traced().dump())

    (call,) = next(s for s in outcome.steps if s.name == "coas_valid").tool_calls
    assert (call.capability, call.shadowed) == ("email.send", True)


def test_the_spec_version_crosses_with_it():
    # A bundle without one cannot be reproduced: the same case against a
    # different spec is a different question.
    outcome = CaseOutcome.from_dump(EVAL_ROW, traced().dump())

    assert (outcome.spec_version, outcome.spec_checksum) == (2, "c37d1f47")


def test_the_eval_row_crosses_verbatim():
    outcome = CaseOutcome.from_dump(EVAL_ROW, traced().dump())

    assert outcome.output == EVAL_ROW


def test_a_malformed_trace_is_refused_rather_than_half_read():
    # Rule 4 enforced rather than described. A step with no name would reach the
    # database as a run_steps row nothing could localise from.
    broken = json.dumps({"spec_version": 1, "spec_checksum": "x", "steps": [{"seq": 1}]})

    with pytest.raises(ValidationError):
        CaseOutcome.from_dump(EVAL_ROW, broken)


def test_an_agent_that_reports_no_trace_is_still_a_result():
    # A build 1 that runs the cases and traces nothing is worse than one that
    # does, but it is not an error — it scores, and the empty TRACE is the
    # finding. Refusing it here would lose the first point on the curve.
    outcome = CaseOutcome(output=EVAL_ROW)

    assert outcome.steps == ()
    assert outcome.declined == ()
