"""Expected against actual, one column at a time.

Per column rather than per row, and the reason is the gate. A patch that fixes
one column and breaks another leaves the row failing before and failing after,
so a row-level comparison sees nothing happen and lets the regression through.
The eval set is a table of counts; comparing it as tables of counts is what
makes "nothing that passed before now fails" a statement about the measurements
rather than about the cases.

**The expected row is the authority on what is measured.** Iterating the agent's
output instead would make the denominator depend on the agent: fill six of seven
columns and the sweep reports 6/6, with the missing column — the actual finding
— counted nowhere.

The same reasoning covers a case that never ran. Its columns are known whether
or not it started, so they are counted, and they are all failures. Summing over
what came back instead scores three cases where one errored as a perfect run
across the two that worked.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Mismatch:
    """One column the agent got wrong, with both readings.

    Both values, never just the column name: the bundle is pasted with no
    further lookup, and `expected 5 / actual 3` is a diagnosis where
    `coa_success failed` is an errand.
    """

    column: str
    expected: Any
    actual: Any


@dataclass(frozen=True)
class Comparison:
    """One eval case, scored.

    `errored` and `mismatched` are not alternatives. A case that raised has
    every column mismatched *and* an error string — the columns because the
    suite still measured them, the string because it is the only thing that
    explains why they are all wrong at once.
    """

    key: str
    expected: Mapping[str, Any] = field(default_factory=dict)
    actual: Mapping[str, Any] = field(default_factory=dict)
    matched: tuple[str, ...] = ()
    mismatched: tuple[Mismatch, ...] = ()
    errored: str | None = None

    def passed(self) -> bool:
        """Whether every column the suite measures agreed."""
        return not self.mismatched and self.errored is None

    def columns(self) -> int:
        """How many columns this case contributes to the score."""
        return len(self.expected)


def compare(
    key: str,
    expected: Mapping[str, Any],
    actual: Mapping[str, Any],
    *,
    errored: str | None = None,
) -> Comparison:
    """Score one case, column by column.

    A column the agent produced and the suite does not measure is ignored:
    `status` is real output that no expected row carries, and counting it would
    let the agent widen its own denominator.
    """
    matched, mismatched = [], []
    for column, want in expected.items():
        got = actual.get(column)
        if errored is None and column in actual and got == want:
            matched.append(column)
        else:
            mismatched.append(Mismatch(column=column, expected=want, actual=got))

    return Comparison(
        key=key,
        expected=dict(expected),
        actual=dict(actual),
        matched=tuple(matched),
        mismatched=tuple(mismatched),
        errored=errored,
    )


def score(results: Sequence[Comparison]) -> tuple[int, int]:
    """Columns that agreed, out of columns the suite measures."""
    return sum(len(r.matched) for r in results), sum(r.columns() for r in results)


def is_empty_sweep(results: Sequence[Comparison]) -> bool:
    """Whether nothing in this sweep ever got as far as counting.

    The trap the repair loop is explicitly warned about: a run returning zeros
    is an empty sweep wearing a comparison. It reports runs, reports no errors,
    and quietly scores every column whose expected value happens to be zero as a
    pass — so the score can read green while nothing reached a check.

    It is a fact about the *sweep* and not about a row, because a single case
    that legitimately counted nothing is an ordinary state — a shipment carrying
    no goods — and flagging it would send somebody to debug a working agent.
    """
    return bool(results) and all(_counted_nothing(result) for result in results)


def _counted_nothing(result: Comparison) -> bool:
    return result.errored is not None or not any(_is_evidence(v) for v in result.actual.values())


def _is_evidence(value: Any) -> bool:
    """Whether a produced value shows the process got somewhere.

    A number is evidence when it is non-zero. Anything else is evidence when it
    is present at all: `status: ACTIVE` means the workflow reached its end and
    returned, and reading "all zero" across non-numeric columns would call that
    empty and send somebody to debug an entry point that worked.
    """
    if isinstance(value, bool):
        return True
    if isinstance(value, int | float):
        return value != 0
    return value not in (None, "", [], {}, ())
