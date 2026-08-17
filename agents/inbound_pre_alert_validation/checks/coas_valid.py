"""Does every batch on the invoice have a matching certificate of analysis?

One file per primitive, because a repair touches one file. That is a rule about
blast radius rather than taste: two primitives in one module means a fix to one
can break the other and the gate has no way to tell which.

Everything process-specific is read out of `spec.lock.json` — the field paths,
the outcome names, where the counts land. Retyping any of them here would give
the spec a second, silently divergent copy.
"""

from __future__ import annotations

from typing import Any

from meridian.runtime import CheckResult
from meridian.runtime.check import (
    apply_fills,
    each_has_matching,
    resolve,
    roll_up,
    rows_failed,
    tally,
)
from meridian.runtime.entities import EntityStore

# How a batch number on a certificate is compared to one on an invoice line.
# Injected rather than called directly: formatting differences nobody mentioned
# are the single most likely thing about build 1 to be wrong, and a repair that
# can swap one function is a different size of change from one that unpicks a
# loop. See `assumptions.json`, `batch_comparison`.
def exact(value: str) -> str:
    """The spec says `each_has_matching`, which is a comparison, not a fuzzy one."""
    return value


def coas_valid(
    config: dict[str, Any],
    store: EntityStore,
    row: dict[str, int],
    *,
    normalise: Any = exact,
) -> CheckResult:
    """Count the batches with a certificate, and name the ones without."""
    criterion = config["criteria"][0]
    left_ref, right_ref = criterion["left"], criterion["right"]["field"]

    rows = resolve(left_ref["entity"], store.instances(left_ref["entity"]), left_ref["path"])
    right = resolve(right_ref["entity"], store.instances(right_ref["entity"]), right_ref["path"])

    # The seam. `each_has_matching` is imported and called from here, so
    # canonicalising the inputs before it is an ordinary patch in this file —
    # there is no constructor argument to look for and none is needed.
    certificates = [normalise(str(cell.value)) for cell in right if cell.value is not None]
    failures = each_has_matching(
        [cell.model_copy(update={"value": normalise(str(cell.value))}) for cell in rows],
        certificates,
        config["scope"],
    )

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
