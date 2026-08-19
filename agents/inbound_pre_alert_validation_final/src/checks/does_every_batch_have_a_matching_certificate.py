"""Does every batch listed on the invoices have a certificate?

One `each_has_matching` criterion, `scope: per_line_item` — so the unit counted
is a batch, which is what makes `coa_total` derived rather than configured. The
invoices say how many certificates to expect and nothing sets a number.

**The matching rule is approved, not assumed.** Review settled it on this card:

    [rule]     the only difference is a trailing lot suffix letter that is
               omitted on the certificate
    [negative] no other differences are ignored — letters, digits, order or
               case differing in any other way is a different batch
    [negative] a batch is not counted twice because its certificate arrived
               more than once; the count is of batches on the invoice

The first two are implemented in `_matches`, and the shape of the second is why
the tolerance is **one-directional**. Stripping the suffix from both sides would
fold `FI5026009A` and `FI5026009B` into one batch, and those differ by more than
an omission — the negative says that is a different batch and must be reported.
So the invoice's value is tried as printed and then with a single trailing lot
letter removed, and the certificate is only ever read as printed.

The third needs no code: rows come from the invoice and candidates go into a
set, so a certificate arriving three times contributes one member and the count
is `len(rows)`. It is satisfied by construction, which is the right way for a
negative to be satisfied.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any, cast

from meridian.domain.primitives import Scope
from meridian.runtime.check.counting import Tally, rows_failed, satisfied, tally
from meridian.runtime.check.paths import Row, resolve
from meridian.runtime.outcome import CheckResult, Failure

KEY = "does_every_batch_have_a_matching_certificate"

_SEPARATORS = re.compile(r"[,;\n]+")

_LOT_SUFFIX = re.compile(r"^(.*\d)[A-Za-z]$")
"""A trailing lot letter, and only after a digit.

Narrow on purpose. `SM8726048A` has one; `UAC25022` does not, because it already
ends in a digit and there is nothing to omit. Anchoring on the digit is what
stops a batch whose real identifier ends in a letter from being truncated.
"""


def does_every_batch_have_a_matching_certificate(
    instances: Mapping[str, Sequence[Mapping[str, Any]]], config: Mapping[str, Any]
) -> tuple[CheckResult, dict[str, Tally]]:
    """Run the check, and report its counts at the grain it examined."""
    scope = cast("Scope", config["scope"])
    criterion = config["criteria"][0]
    left, right = criterion["left"], criterion["right"]["field"]

    rows = _per_batch(resolve(left["entity"], instances.get(left["entity"], ()), left["path"]))
    certificates = resolve(right["entity"], instances.get(right["entity"], ()), right["path"])
    # A set, so a certificate that arrived three times is one candidate. That is
    # the third negative on this card, satisfied by the data structure.
    available = {
        value for certificate in certificates for value in _values(certificate.value)
    }

    failures = [
        Failure(
            grain=scope,
            locator=row.locator,
            reason="no certificate carries this batch",
            subject=str(row.value),
            detail={"value": row.value, "available": sorted(available)},
        )
        for row in rows
        if not _matches(row.value, available)
    ]

    failed = rows_failed(rows, failures)
    counted = tally(rows, failed)
    held = satisfied(str(config["quantifier"]), counted.passed, counted.total)

    return (
        CheckResult(
            outcome=_outcome(config, held=held, examined=counted.total),
            total=counted.total,
            passed=counted.passed,
            failed=counted.failed,
            failures=tuple(failures),
        ),
        {scope: counted},
    )


def _matches(value: Any, available: set[str]) -> bool:
    """Whether some certificate carries this batch, per the settled rule.

    Exactly as printed first, because that is what `each_has_matching` means and
    what the negative protects. Then once more with a single trailing lot letter
    removed, because the process owner said the certificates omit it.

    Nothing else. No case folding, no trimming inside the value, no similarity.
    Each of those would be a rule about when two spellings mean one batch, and
    the card already says which one spelling difference counts.
    """
    printed = str(value)
    if printed in available:
        return True
    without = _LOT_SUFFIX.match(printed)
    return without is not None and without.group(1) in available


def _values(value: Any) -> list[str]:
    """The batch numbers written in one cell.

    An invoice line covers a drug and one drug ships as several lots, so the
    supplier prints `SM8726048A, SM8726049A, SM8726050A` in a single batch cell.
    Read whole that is one batch nobody has a certificate for; read as three it
    is three batches with three certificates, which is what `coa_total` counts.

    Splitting only — a separator is not a spelling, so this is not the kind of
    difference the negative on this card forbids ignoring.
    """
    if not isinstance(value, str):
        return [] if value is None else [str(value)]
    return [part.strip() for part in _SEPARATORS.split(value) if part.strip()]


def _per_batch(rows: Sequence[Row]) -> tuple[Row, ...]:
    """One row per batch, so the unit counted is a batch and not a line item.

    A split row needs its own `indices`, because `(document, indices)` is the
    place a tally counts. Three batches sharing one line item's indices would
    collapse back into a single place and report `total: 1`.
    """
    expanded: list[Row] = []
    for row in rows:
        values = _values(row.value)
        if len(values) <= 1:
            expanded.append(row)
            continue
        expanded.extend(
            replace(row, value=value, locator=f"{row.locator}[{n}]", indices=(*row.indices, n))
            for n, value in enumerate(values)
        )
    return tuple(expanded)


def _outcome(config: Mapping[str, Any], *, held: bool, examined: int) -> str:
    """The lowest-priority outcome that applies.

    Two declared outcomes and one distinguishable result, so the mapping is
    total. Note what is *not* here: the old board declared a third outcome for
    *a certificate exists but its batch disagrees*, which no criterion could
    tell from *no certificate at all*. Review settled that too — both are the
    same discrepancy and take the same path — and the board now declares two
    outcomes, so there is nothing left to guess.

    Examining nothing is not passing: `all` over zero rows is vacuously true,
    and `on_missing_input: fail` says so.
    """
    ordered = sorted(config["outcomes"], key=lambda o: int(o.get("priority", 0)))
    if not examined and str(config.get("on_missing_input")) == "fail":
        return str(ordered[-1]["name"])
    return str(ordered[0]["name"] if held else ordered[-1]["name"])
