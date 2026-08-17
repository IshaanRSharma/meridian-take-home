"""Does every line item on the invoice carry all four codes?

One rule reported at two grains, which is what `fills[].per` is for. A line item
fails when any of its four codes is absent; an invoice fails when any of its
line items does. Both are real eval columns — `goods_failed` counts line items,
`invoices_failed` counts the documents containing them — and neither is derived
from the other after the fact.
"""

from __future__ import annotations

from typing import Any

from meridian.runtime import CheckResult
from meridian.runtime.check import apply_fills, present, resolve, roll_up, rows_failed, tally
from meridian.runtime.entities import EntityStore


def invoice_complete(
    config: dict[str, Any], store: EntityStore, row: dict[str, int]
) -> CheckResult:
    """Count the line items missing a code, and the invoices carrying them."""
    rows, failures = [], []
    for criterion in config["criteria"]:
        left = criterion["left"]
        cells = resolve(left["entity"], store.instances(left["entity"]), left["path"])
        rows += cells
        failures += present(cells, config["scope"])

    bad = rows_failed(rows, failures)
    apply_fills(
        row,
        config["fills"],
        {"per_line_item": tally(rows, bad), "per_document": roll_up(rows, bad)},
        config["scope"],
    )

    counts = tally(rows, bad)
    ordered = sorted(config["outcomes"], key=lambda o: o["priority"])
    return CheckResult(
        outcome=ordered[0]["name"] if not bad else ordered[1]["name"],
        total=counts.total,
        passed=counts.passed,
        failed=counts.failed,
        failures=tuple(failures[:5]),
    )
