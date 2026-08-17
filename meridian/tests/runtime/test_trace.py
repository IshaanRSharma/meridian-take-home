"""The trajectory a failure bundle is assembled from.

The bundle is the product: an engineer pastes it into a coding agent and the
diagnosis has to be in the paste. These tests hold the trace to that standard
rather than to "it recorded something".
"""

from meridian.runtime.outcome import CheckResult
from meridian.runtime.trace import RunTrace


def test_a_step_that_failed_is_still_in_the_trace():
    # The single most important property here. A trace that only records
    # successes omits exactly the step somebody needed to read.
    trace = RunTrace(spec_version=1, spec_checksum="1e77")
    with trace.step("coas_valid") as step:
        step.failed(RuntimeError("2 unmatched"))

    assert [s.name for s in trace.steps] == ["coas_valid"]
    assert trace.steps[0].status == "failed"
    assert "2 unmatched" in (trace.steps[0].error or "")


def test_a_step_that_raises_is_recorded_before_the_error_propagates():
    trace = RunTrace(spec_version=1, spec_checksum="1e77")
    try:
        with trace.step("extract_invoice"):
            raise RuntimeError("model returned invalid json")
    except RuntimeError:
        pass

    assert trace.steps[0].status == "failed"
    assert "invalid json" in (trace.steps[0].error or "")


def test_steps_keep_the_order_they_ran_in():
    # "step 3 ok, step 4 ok, step 5 FAIL" is how a human reads a trajectory.
    trace = RunTrace(spec_version=1, spec_checksum="1e77")
    for name in ("extract_invoice", "extract_coa", "coas_valid"):
        with trace.step(name):
            pass
    assert [s.seq for s in trace.steps] == [1, 2, 3]
    assert [s.name for s in trace.steps] == ["extract_invoice", "extract_coa", "coas_valid"]


def test_a_check_result_lands_in_the_trace_as_its_counts():
    trace = RunTrace(spec_version=1, spec_checksum="1e77")
    with trace.step("coas_valid") as step:
        step.produced(CheckResult(outcome="missing_coa", total=5, passed=3, failed=2))

    recorded = trace.steps[0].output
    assert recorded == {"outcome": "missing_coa", "total": 5, "passed": 3, "failed": 2}


def test_tool_calls_are_attributed_to_the_step_that_made_them():
    # "which step sent the email" is the question, and a flat list cannot answer it.
    trace = RunTrace(spec_version=1, spec_checksum="1e77")
    with trace.step("report_coa_discrepancy") as step:
        step.tool_called("email.send", {"to": "receiving_supervisor"}, shadowed=True)

    assert trace.steps[0].tool_calls[0].capability == "email.send"
    assert trace.steps[0].tool_calls[0].shadowed is True


def test_the_trace_names_the_spec_it_ran_against():
    # A bundle without a spec version cannot be reproduced, and the repair loop
    # would not know whether the rule changed underneath it.
    trace = RunTrace(spec_version=3, spec_checksum="1e77abcd")
    assert trace.spec_version == 3
    assert trace.spec_checksum == "1e77abcd"


def test_a_retried_step_records_each_attempt():
    trace = RunTrace(spec_version=1, spec_checksum="1e77")
    with trace.step("verify_license", attempt=1) as first:
        first.failed(TimeoutError("no answer"))
    with trace.step("verify_license", attempt=2):
        pass

    assert [s.attempt for s in trace.steps] == [1, 2]
    assert [s.status for s in trace.steps] == ["failed", "ok"]
