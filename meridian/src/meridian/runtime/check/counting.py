"""Counting rows, and rolling those counts up to a coarser grain.

**A Check counts rows, not assertions.** Four criteria over five line items is
five things checked, not twenty. This is the resolution of a real ambiguity: an
earlier hand-written agent reported ``total: 20`` for a check whose sibling
reported ``total: 5`` at the same ``scope``, which made ``goods_failed`` mean
either *failed assertions* or *failed line items* depending on which check you
read. Rows is the reading that makes the two agree, and it is what
``quantifier`` was always for — ``all`` means every criterion must hold **for a
row to pass**.

Rolling up is one rule: a coarser unit passes exactly when every finer unit
beneath it passes. That single rule turns one criterion into two eval columns.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from meridian.runtime.check.paths import Row
from meridian.runtime.outcome import Failure

# `count` reports a number rather than asserting anything, so it is always
# satisfied — the count itself is the answer.
_QUANTIFIERS: dict[str, Callable[[int, int], bool]] = {
    "all": lambda held, total: held == total,
    "any": lambda held, _total: held > 0,
    "none": lambda held, _total: held == 0,
    "count": lambda _held, _total: True,
}


@dataclass(frozen=True)
class Tally:
    """What a Check reports at one grain."""

    total: int
    passed: int
    failed: int


def rows_failed(
    rows: Sequence[Row], failures: Iterable[Failure]
) -> set[tuple[int, tuple[int, ...]]]:
    """Which places failed, by ``(document, indices)`` rather than by locator.

    Two criteria fail at the same place when a line item is missing two codes.
    Keying on the locator would count that twice and report two failed line
    items where there is one.
    """
    by_locator = {row.locator: (row.document, row.indices) for row in rows}
    return {by_locator[f.locator] for f in failures if f.locator in by_locator}


def tally(rows: Sequence[Row], failed: set[tuple[int, tuple[int, ...]]]) -> Tally:
    """Count the distinct places examined, and how many held."""
    places = {(row.document, row.indices) for row in rows}
    bad = places & failed
    return Tally(total=len(places), passed=len(places - bad), failed=len(bad))


def roll_up(rows: Sequence[Row], failed: set[tuple[int, tuple[int, ...]]]) -> Tally:
    """The same counts one grain coarser: per document rather than per row.

    A document passes exactly when every row beneath it passed. This is the
    whole mechanism behind one rule producing both ``goods_failed`` (line items)
    and ``invoices_failed`` (the documents containing them).
    """
    documents = {row.document for row in rows}
    bad = {document for document, _ in failed}
    return Tally(total=len(documents), passed=len(documents - bad), failed=len(documents & bad))


def satisfied(quantifier: str, held: int, total: int) -> bool:
    """Whether a Check's quantifier is met over the rows it examined."""
    return _QUANTIFIERS.get(quantifier, _QUANTIFIERS["all"])(held, total)
