"""Counting rows, and rolling them up.

These encode the resolution of a real ambiguity found by running a hand-written
agent: a Check counts the *places it examined*, never the assertions it made.
Four criteria over five line items is five things checked, not twenty.
"""

from meridian.runtime.check.counting import Tally, roll_up, rows_failed, satisfied, tally
from meridian.runtime.check.criteria import each_has_matching, present
from meridian.runtime.check.paths import resolve

# Two line items incomplete, and one of them missing TWO codes — which is the
# case that tells the two readings apart.
INVOICE = [
    {
        "line_items": [
            {"hts_number": "3004.90", "anda_number": "A1"},
            {"hts_number": None, "anda_number": None},
            {"hts_number": "3004.90", "anda_number": "A3"},
        ]
    }
]


def _failed_places(paths: list[str]):
    rows, failures = [], []
    for path in paths:
        found = resolve("ci", INVOICE, path)
        rows += found
        failures += present(found, "per_line_item")
    return rows, rows_failed(rows, failures)


def test_a_check_counts_places_examined_not_assertions_made():
    # Two criteria over three line items is THREE things checked. Twenty would
    # be the answer if this counted assertions, and then `goods_failed` would
    # mean something different in this check than in its sibling.
    rows, failed = _failed_places(["line_items[].hts_number", "line_items[].anda_number"])
    assert tally(rows, failed) == Tally(total=3, passed=2, failed=1)


def test_one_line_item_missing_two_codes_is_one_failed_line_item():
    # The case that distinguishes the two readings. Keying failures on the
    # locator would count this twice and report two incomplete line items where
    # the invoice has one.
    _, failed = _failed_places(["line_items[].hts_number", "line_items[].anda_number"])
    assert len(failed) == 1


def test_a_document_fails_when_any_row_beneath_it_fails():
    rows, failed = _failed_places(["line_items[].hts_number"])
    assert roll_up(rows, failed) == Tally(total=1, passed=0, failed=1)


def test_a_document_passes_only_when_every_row_passes():
    clean = [{"line_items": [{"hts_number": "1"}, {"hts_number": "2"}]}]
    rows = resolve("ci", clean, "line_items[].hts_number")
    assert roll_up(rows, rows_failed(rows, present(rows, "per_line_item"))) == Tally(1, 1, 0)


def test_two_documents_are_counted_separately():
    two = [{"line_items": [{"hts_number": "1"}]}, {"line_items": [{"hts_number": None}]}]
    rows = resolve("ci", two, "line_items[].hts_number")
    assert roll_up(rows, rows_failed(rows, present(rows, "per_line_item"))) == Tally(2, 1, 1)


def test_a_matching_failure_shows_both_sides():
    # The diagnosis IS the pair. 'UAC25022 ' against ['UAC25022'] shows trailing
    # whitespace without anybody explaining it.
    invoice = [{"line_items": [{"batch_no": "UAC25022 "}]}]
    rows = resolve("ci", invoice, "line_items[].batch_no")
    (failure,) = each_has_matching(rows, ["UAC25022"], "per_line_item")
    assert failure.detail["value"] == "UAC25022 "
    assert failure.detail["available"] == ["UAC25022"]


def test_matching_is_exact_because_the_criterion_says_nothing_else():
    # Normalising here would invent a rule the process owner never stated.
    # Build 1 is EXPECTED to fail on this, and that failure is the first repair.
    invoice = [{"line_items": [{"batch_no": "uac25019"}]}]
    rows = resolve("ci", invoice, "line_items[].batch_no")
    assert len(each_has_matching(rows, ["UAC25019"], "per_line_item")) == 1


def test_the_quantifier_decides_what_satisfied_means():
    assert satisfied("all", held=3, total=3)
    assert not satisfied("all", held=2, total=3)
    assert satisfied("any", held=1, total=3)
    assert satisfied("none", held=0, total=3)
    assert not satisfied("none", held=1, total=3)
