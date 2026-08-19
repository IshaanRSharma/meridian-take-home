"""Does every invoice line carry HTS, FDA product code, NDC and ANDA?

Four `present` criteria over one row set, `scope: per_line_item`,
`quantifier: all` — so a line item passes only when all four codes are there.

**Four criteria over five line items is five things checked, not twenty**, and
the review settled that explicitly: *"Count failed line items, not failed codes:
one line item missing any number of the required codes counts as one goods
failure."* `rows_failed` keys on `(document, indices)` — the *place* a failure
happened — so a line missing three codes collapses to one failed place, which is
what makes that assertion true rather than merely intended.

This is the check that reports at two grains. `goods_failed` counts line items;
`invoices_total`, `invoices_successful` and `invoices_failed` count the invoices
containing them, and an invoice fails when any one of its line items does. One
rule, four eval columns, and the roll-up is what makes them agree.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

from meridian.domain.primitives import Scope
from meridian.runtime.check.counting import Tally, roll_up, rows_failed, satisfied, tally
from meridian.runtime.check.criteria import present
from meridian.runtime.check.paths import Row, resolve
from meridian.runtime.outcome import CheckResult, Failure

KEY = "does_every_invoice_line_carry_all_four_codes"


def does_every_invoice_line_carry_all_four_codes(
    instances: Mapping[str, Sequence[Mapping[str, Any]]], config: Mapping[str, Any]
) -> tuple[CheckResult, dict[str, Tally]]:
    """Run the check, and report its counts at both grains.

    The tallies come back beside the result because `apply_fills` needs the
    per-document roll-up as well as the per-row count, and recomputing it from a
    `CheckResult` is impossible — the coarser number is not derivable from the
    finer one.
    """
    scope = cast("Scope", config["scope"])
    examined: list[Row] = []
    failed: set[tuple[int, tuple[int, ...]]] = set()
    failures: list[Failure] = []

    for criterion in config["criteria"]:
        left = criterion["left"]
        rows = resolve(left["entity"], instances.get(left["entity"], ()), left["path"])
        found = present(rows, scope)
        examined.extend(rows)
        failed |= rows_failed(rows, found)
        failures.extend(found)

    per_row = tally(examined, failed)
    per_document = roll_up(examined, failed)
    held = satisfied(str(config["quantifier"]), per_row.passed, per_row.total)

    return (
        CheckResult(
            outcome=_outcome(config, held=held, examined=per_row.total),
            total=per_row.total,
            passed=per_row.passed,
            failed=per_row.failed,
            failures=tuple(failures),
        ),
        {scope: per_row, "per_document": per_document},
    )


def _outcome(config: Mapping[str, Any], *, held: bool, examined: int) -> str:
    """The lowest-priority outcome that applies.

    Two outcomes and one distinguishable result, so the mapping is total: the
    quantifier held or it did not.

    **Examining nothing is not passing.** `all` over zero rows is vacuously
    satisfied, so an invoice that never arrived would report `pass` and fill
    every column with a confident zero — a green row for a shipment nobody
    read. The card says `on_missing_input: fail`, which is exactly this case,
    so zero rows takes the failing branch.
    """
    ordered = sorted(config["outcomes"], key=lambda o: int(o.get("priority", 0)))
    if not examined and str(config.get("on_missing_input")) == "fail":
        return str(ordered[-1]["name"])
    return str(ordered[0]["name"] if held else ordered[-1]["name"])
