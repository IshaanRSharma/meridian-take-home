"""The shape every Check returns.

A Check reports counts, not a boolean, because the deliverable is a per-case row
of aggregates — ``CoA (Success) 5 / CoA (Total) 5``. These tests hold the type to
the arithmetic that row depends on.
"""

import pytest
from pydantic import ValidationError

from meridian.runtime.outcome import CheckResult, Failure


def test_every_examined_row_either_passed_or_failed():
    # The eval row is arithmetic: total must reconcile, or a column is silently
    # wrong and no test downstream would notice.
    with pytest.raises(ValidationError, match="passed"):
        CheckResult(outcome="pass", total=5, passed=3, failed=1)


def test_a_check_that_examined_nothing_is_not_a_check_that_passed():
    # Zero line items is a real state — an invoice with no goods. It must be
    # distinguishable from five that all passed, because one is a data problem.
    nothing = CheckResult(outcome="pass", total=0, passed=0, failed=0)
    assert nothing.total == 0
    assert not nothing.examined_anything()


def test_a_failure_carries_enough_to_diagnose_without_a_lookup():
    # The failure bundle is the product. A failure that says "row 3 failed"
    # sends a human back to the source documents; one that shows the values
    # is solved in a single read.
    failure = Failure(
        grain="per_line_item",
        locator="commercial_invoice.line_items[2].batch_no",
        detail={"expected_in": ["UAC25019"], "actual": "UAC25022 "},
        reason="no certificate carries this batch",
    )
    assert "UAC25022 " in str(failure.detail)
    assert failure.locator.endswith("batch_no")


def test_the_outcome_is_a_name_the_board_declared():
    # Not a boolean and not an enum this module owns: the process owner names
    # their own outcomes, and codegen routes on those names.
    assert CheckResult(outcome="missing_coa", total=5, passed=3, failed=2).outcome == "missing_coa"


def test_a_result_cannot_be_edited_after_it_is_returned():
    # It is evidence. A later step adjusting a count would make the trace lie.
    result = CheckResult(outcome="pass", total=1, passed=1, failed=0)
    with pytest.raises(ValidationError):
        result.passed = 0


def test_counts_are_never_negative():
    with pytest.raises(ValidationError):
        CheckResult(outcome="pass", total=-1, passed=0, failed=0)
