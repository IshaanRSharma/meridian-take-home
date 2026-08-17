"""Per column, and what that buys.

A patch that fixes one column and breaks another is invisible at row
granularity — the row was failing before and is failing after, and the gate sees
nothing. Every property here is a case where per-row and per-column give
different answers, or where a plausible implementation scores something green
that is not.
"""

from __future__ import annotations

from meridian.healing.compare import compare, is_empty_sweep, score

EXPECTED = {
    "invoices_successful": 0,
    "invoices_failed": 1,
    "invoices_total": 1,
    "goods_failed": 2,
    "failed_coa": 0,
    "coa_success": 5,
    "coa_total": 5,
}


def test_a_column_that_agrees_is_matched_and_one_that_does_not_is_not():
    result = compare("CAAU4056270", EXPECTED, EXPECTED | {"coa_success": 3})

    assert "coa_total" in result.matched
    assert [(m.column, m.expected, m.actual) for m in result.mismatched] == [("coa_success", 5, 3)]


def test_a_column_the_eval_expects_and_the_agent_never_produced_is_a_mismatch():
    # The one a reasonable implementation gets wrong. Iterating the agent's
    # output and comparing what it happens to contain scores a missing column as
    # nothing at all — so an agent that fills six of seven columns reads as
    # 6/6 green, and the column nobody filled is the finding.
    without = {k: v for k, v in EXPECTED.items() if k != "goods_failed"}

    result = compare("CAAU4056270", EXPECTED, without)

    assert [(m.column, m.actual) for m in result.mismatched] == [("goods_failed", None)]


def test_a_column_the_agent_produces_and_the_eval_does_not_measure_is_ignored():
    # The process may produce more than the suite scores — `status` is real
    # output and no expected row here carries it. Counting it would make the
    # denominator depend on the agent rather than on the suite.
    result = compare("CAAU4056270", EXPECTED, EXPECTED | {"status": "ACTIVE"})

    assert result.mismatched == ()
    assert "status" not in result.matched


def test_an_errored_case_fails_every_column_rather_than_shrinking_the_denominator():
    # The subtle one. A case that never ran has no columns to compare, so
    # summing over what came back scores three cases where one errored as
    # 14/14 — a perfect run that never touched a third of the suite. The
    # expected row is known whether or not the agent started, so the
    # denominator is known too.
    result = compare("CAAU4056270", EXPECTED, {}, errored="TimeoutError: 60s")

    assert result.matched == ()
    assert len(result.mismatched) == len(EXPECTED)
    assert score([result]) == (0, 7)


def test_the_score_is_columns_across_every_case():
    passing = compare("A", EXPECTED, EXPECTED)
    failing = compare("B", EXPECTED, EXPECTED | {"coa_success": 3, "coa_total": 4})

    assert score([passing, failing]) == (12, 14)


def test_a_sweep_where_every_case_counted_nothing_is_flagged_however_it_scores():
    # The trap from the loop's own stop conditions: a run returning zeros is an
    # empty sweep wearing a comparison. Every column whose expected value
    # happens to be zero scores as a pass, so the score can look respectable
    # while nothing ever reached a check.
    zeroed = dict.fromkeys(EXPECTED, 0)
    results = [compare(k, zeroed, zeroed) for k in ("A", "B", "C")]

    assert score(results) == (21, 21)
    assert is_empty_sweep(results)


def test_one_case_that_legitimately_counted_nothing_is_not_an_empty_sweep():
    # A shipment carrying nothing is a real state. The tell is EVERY case, which
    # is why it is a fact about the sweep and not about a row.
    zeroed = dict.fromkeys(EXPECTED, 0)
    results = [
        compare("A", zeroed, zeroed),
        compare("B", EXPECTED, EXPECTED),
    ]

    assert not is_empty_sweep(results)


def test_a_sweep_of_only_errors_is_empty_too():
    results = [compare(k, EXPECTED, {}, errored="ImportError: no module") for k in ("A", "B")]

    assert is_empty_sweep(results)


def test_a_string_column_counts_as_produced_even_when_the_numbers_are_zero():
    # `status: ACTIVE` means the workflow reached its end and returned. Reading
    # "all zero" over non-numeric columns would call that empty and send
    # somebody to debug an entry point that worked.
    expected = {"goods_failed": 0, "status": "ACTIVE"}
    results = [compare("A", expected, expected)]

    assert not is_empty_sweep(results)
