"""Does every batch on the invoice have a matching COA?

One `each_has_matching` criterion: every batch number the invoice lists must
appear on some certificate. `scope: per_line_item`, so the unit counted is a
batch, which is what makes `coa_total` derived rather than configured — the
invoice says how many certificates to expect and nothing sets a number.

**Matching is normalised, and the spec is what says so.** The card carries a
settled assertion — *"Batch numbers are matched after trimming spaces and
ignoring case"* — so trimming here implements approved knowledge rather than
inventing a rule. The kernel compares exactly by design; normalising is done to
its inputs and the failures are restored to what was actually printed, because
`'UAC25022 '` against `['UAC25022']` is the diagnosis and the normalised form
hides it.

**Three outcomes, two distinguishable results.** Nothing in an
`each_has_matching` criterion can tell *a batch has no COA* from *a COA exists
but its batch number disagrees* — that is a stop condition, and it is reported
in assumptions.json. It is not guessed at with a similarity threshold. What
makes it safe to proceed is that both outcomes route to the same Action on this
board, so the choice changes nothing anything can observe.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any, cast

from meridian.domain.primitives import Scope
from meridian.runtime.check.counting import Tally, rows_failed, satisfied, tally
from meridian.runtime.check.criteria import each_has_matching
from meridian.runtime.check.paths import Row, resolve
from meridian.runtime.outcome import CheckResult, Failure

KEY = "coas_valid"

UNMATCHED_OUTCOME = "missing_coa"
"""Which of the two indistinguishable outcomes an unmatched batch reports.

Chosen rather than derived. Edges e5 and e6 both lead to
`report_coa_discrepancy`, so this selects the label on a branch and not the
branch, and no eval column separates the two.
"""


def normalise(value: Any) -> Any:
    """The form two spellings of one batch number share.

    Trimming and case only, exactly what the assertion states. Anything further
    — stripping a suffix, ignoring punctuation — would be a rule nobody stated.
    """
    return value.strip().lower() if isinstance(value, str) else value


def coas_valid(
    instances: Mapping[str, Sequence[Mapping[str, Any]]], config: Mapping[str, Any]
) -> tuple[CheckResult, dict[str, Tally]]:
    """Run the check, and report its counts at the grain it examined."""
    scope = cast("Scope", config["scope"])
    criterion = config["criteria"][0]
    left, right = criterion["left"], criterion["right"]["field"]

    rows = resolve(left["entity"], instances.get(left["entity"], ()), left["path"])
    candidates = resolve(right["entity"], instances.get(right["entity"], ()), right["path"])

    failures = _restored(
        each_has_matching(
            [replace(row, value=normalise(row.value)) for row in rows],
            {normalise(candidate.value) for candidate in candidates},
            scope,
        ),
        rows,
    )

    failed = rows_failed(rows, failures)
    counted = tally(rows, failed)
    held = satisfied(str(config["quantifier"]), counted.passed, counted.total)

    return (
        CheckResult(
            outcome=_outcome(config, held=held),
            total=counted.total,
            passed=counted.passed,
            failed=counted.failed,
            failures=tuple(failures),
        ),
        {scope: counted},
    )


def _restored(failures: Sequence[Failure], rows: Sequence[Row]) -> list[Failure]:
    """Put the printed value back into a failure that matched on a folded one.

    A bundle has to show what was on the page. `'UAC25022 '` and `'uac25019'`
    diagnose themselves; `'uac25022'` reads as a mystery.
    """
    printed = {row.locator: row.value for row in rows}
    return [
        # `Failure` is a pydantic model, not a dataclass — dataclasses.replace
        # raises on it, and only on the path where something actually failed.
        failure.model_copy(
            update={
                "subject": str(printed.get(failure.locator, failure.subject)),
                "detail": {**failure.detail, "value": printed.get(failure.locator)},
            }
        )
        for failure in failures
    ]


def _outcome(config: Mapping[str, Any], *, held: bool) -> str:
    """The lowest-priority outcome that applies."""
    if held:
        ordered = sorted(config["outcomes"], key=lambda o: int(o.get("priority", 0)))
        return str(ordered[0]["name"])
    return UNMATCHED_OUTCOME
