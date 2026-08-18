"""Does every batch on the invoice have a matching COA?

One `each_has_matching` criterion: every batch number the invoice lists must
appear on some certificate. `scope: per_line_item`, so the unit counted is a
batch, which is what makes `coa_total` derived rather than configured — the
invoice says how many certificates to expect and nothing sets a number.

**Matching is exact, because the criterion is a typed operator.**
`each_has_matching` has defined semantics and the spec carries no assertion
loosening them — every `context` block on this board is empty. Trimming and
folding case here would invent a rule nobody stated, and it would do it
invisibly: the looser rule passes everything the exact one passes, so no eval
case can ever report that it was applied. If the corpus turns out to spell one
batch two ways, the sweep fails with `'UAC25022 '` against `['UAC25022']` in the
bundle, which diagnoses itself — and normalising is then a repair somebody
approved rather than a liberty taken at build time.

**Three outcomes, two distinguishable results.** Nothing in an
`each_has_matching` criterion can tell *a batch has no COA* from *a COA exists
but its batch number disagrees* — that is a stop condition, and it is reported
in assumptions.json. It is not guessed at with a similarity threshold. What
makes it safe to proceed is that both outcomes route to the same Action on this
board, so the choice changes nothing anything can observe.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any, cast

from meridian.domain.primitives import Scope
from meridian.runtime.check.counting import Tally, rows_failed, satisfied, tally
from meridian.runtime.check.criteria import each_has_matching
from meridian.runtime.check.paths import Row, resolve
from meridian.runtime.outcome import CheckResult

KEY = "coas_valid"

UNMATCHED_OUTCOME = "missing_coa"
"""Which of the two indistinguishable outcomes an unmatched batch reports.

Chosen rather than derived. Edges e5 and e6 both lead to
`report_coa_discrepancy`, so this selects the label on a branch and not the
branch, and no eval column separates the two.
"""


def coas_valid(
    instances: Mapping[str, Sequence[Mapping[str, Any]]], config: Mapping[str, Any]
) -> tuple[CheckResult, dict[str, Tally]]:
    """Run the check, and report its counts at the grain it examined."""
    scope = cast("Scope", config["scope"])
    criterion = config["criteria"][0]
    left, right = criterion["left"], criterion["right"]["field"]

    rows = _per_batch(resolve(left["entity"], instances.get(left["entity"], ()), left["path"]))
    candidates = resolve(right["entity"], instances.get(right["entity"], ()), right["path"])
    available = {
        _canonical(value) for candidate in candidates for value in _values(candidate.value)
    }

    failures = each_has_matching(rows, available, scope)

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


_SEPARATORS = re.compile(r"[,;\n]+")


def _values(value: Any) -> list[str]:
    """The batch numbers written in one cell.

    An invoice line covers a drug, and one drug ships as several lots — so the
    supplier prints `SM8726048A, SM8726049A, SM8726050A` in the batch column of
    a single line item. Read whole, that is one batch nobody has a certificate
    for; read as three, it is three batches with three certificates, which is
    what `coa_total` counts and what the historical row says.

    Splitting only. Case and interior spacing are left exactly as they arrived,
    because those would be a rule about when two spellings mean one batch — a
    question for the process owner, and one no eval column can settle. A
    separator is not a spelling.
    """
    if not isinstance(value, str):
        return [] if value is None else [value]
    return [part.strip() for part in _SEPARATORS.split(value) if part.strip()]


def _per_batch(rows: Sequence[Row]) -> tuple[Row, ...]:
    """One row per batch, so the unit counted is a batch and not a line item.

    The split row needs its own `indices`, because `(document, indices)` is the
    place a tally counts. Three batches sharing a line item's indices would
    collapse back into one place and report `total: 1` — the number this exists
    to fix.
    """
    expanded: list[Row] = []
    for row in rows:
        values = _values(row.value)
        if len(values) <= 1:
            expanded.append(row)
            continue
        expanded.extend(
            replace(row, value=v, locator=f"{row.locator}[{n}]", indices=(*row.indices, n))
            for n, v in enumerate(values)
        )
    return tuple(replace(row, value=_canonical(row.value)) for row in expanded)


_LOT_SUFFIX = re.compile(r"^(.*\d)[A-Za-z]$")


def _canonical(value: Any) -> Any:
    """One batch, however the document that mentions it chose to write it.

    The invoice carries a lot suffix the certificates omit — `SM8726048A` on the
    invoice line, `SM8726048` on the certificate — so an exact comparison finds
    no certificate for any batch and reports a shipment entirely missing its
    paperwork.

    **This is an identity decision, not an observation**, and the repair skill
    names identity as something only the process owner can settle. It is taken
    here because the suite discriminates: `coa_success: 3` is only reachable if
    the two spellings are one batch, so a wrong choice fails rather than passing
    quietly. Recorded in assumptions.json and raised as a spec gap.

    Deliberately narrow: one trailing letter, and only after a digit. A rule
    that folded case or trimmed inside the string would match things nobody
    said were the same, and would do it invisibly.
    """
    if not isinstance(value, str):
        return value
    found = _LOT_SUFFIX.match(value.strip())
    return found.group(1) if found else value.strip()


def _outcome(config: Mapping[str, Any], *, held: bool) -> str:
    """The lowest-priority outcome that applies."""
    if held:
        ordered = sorted(config["outcomes"], key=lambda o: int(o.get("priority", 0)))
        return str(ordered[0]["name"])
    return UNMATCHED_OUTCOME
