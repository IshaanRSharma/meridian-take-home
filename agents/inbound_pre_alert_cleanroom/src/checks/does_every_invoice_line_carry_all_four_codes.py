"""Every line item on every commercial invoice carries all four codes.

Four ``present`` criteria at one grain. The whole rule is on the card — which
paths, which grain, which counts land in which column — so this file reads the
criteria rather than naming the four fields, and a fifth code added to the board
would need no change here.

The card asks for counts at two grains from one rule: ``goods_failed`` at the
Check's own ``per_line_item`` scope, and three invoice columns rolled up one
grain coarser. A document passes exactly when every line item under it passes,
which is the card's own sentence: *"an invoice fails if any of its lines does."*
And the entity context settles what a failure is counted in: *"Count failed line
items, not failed codes"* — which is why the four criteria contribute rows to
one pool rather than four independent tallies.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from checking import (
    Place,
    counted,
    evidence_for,
    missing_inputs,
    outcome_names,
    roll_up_over_instances,
    rows_for,
    scope_of,
)
from findings import Checked, Discrepancy

from meridian.runtime import CheckResult, EntityStore, Failure
from meridian.runtime.check import apply_fills, present, rows_failed, satisfied, tally
from meridian.runtime.check.counting import Tally
from meridian.runtime.check.paths import Row

_OP = "present"


def does_every_invoice_line_carry_all_four_codes(
    store: EntityStore, card: Mapping[str, Any]
) -> Checked:
    """Count the line items missing any required code, and the invoices holding them.

    Args:
        store: every entity instance gathered for this shipment.
        card: this primitive's ``config`` from the frozen spec.

    Returns:
        The counts, the columns they fill, and the line items to report.
    """
    scope = scope_of(card)
    passing, failing = outcome_names(card)
    documents = str(card["inputs"][0])

    absent = missing_inputs(store, [str(name) for name in card["inputs"]])
    if absent:
        # `on_missing_input: fail`. Nothing arrived to examine, and the process
        # owner said that is a failure rather than something to wait through.
        return _nothing_arrived(card, failing, absent)

    rows: list[Row] = []
    failures: list[Failure] = []
    for criterion in card["criteria"]:
        if criterion["op"] != _OP:
            raise NotImplementedError(
                f"this Check implements {_OP!r}; the spec now asks for {criterion['op']!r}, "
                "which needs a regenerated agent rather than a silent skip"
            )
        found = rows_for(store, criterion["left"])
        rows.extend(found)
        failures.extend(present(found, scope))

    failed: set[Place] = rows_failed(rows, failures)
    per_line = tally(rows, failed)
    tallies: dict[str, Tally] = {
        scope: per_line,
        "per_document": roll_up_over_instances(failed, len(store.instances(documents))),
    }

    held = satisfied(str(card["quantifier"]), per_line.passed, per_line.total)
    result = CheckResult(
        outcome=passing if held else failing,
        total=per_line.total,
        passed=per_line.passed,
        failed=per_line.failed,
        failures=tuple(failures),
    )
    return Checked(
        result=result,
        fills=counted(apply_fills({}, card["fills"], tallies, scope, failures)),
        discrepancies=tuple(
            Discrepancy(place, result.outcome, evidence_for(store, card["evidence"], place))
            for place in sorted(failed)
        ),
    )


def _nothing_arrived(card: Mapping[str, Any], failing: str, absent: tuple[str, ...]) -> Checked:
    """The failing outcome with honest zeros, and the reason recorded as a failure.

    Zero examined is deliberately not folded into ``passed``: a shipment whose
    invoices never arrived and one whose invoices were all clean would otherwise
    be the same row, and only one of them is a problem with the mailbox.
    """
    scope = scope_of(card)
    failure = Failure(
        grain=scope,
        locator=absent[0],
        reason="a declared input never arrived, so there was nothing to examine",
        subject=absent[0],
        detail={"missing_inputs": list(absent)},
    )
    empty = Tally(total=0, passed=0, failed=0)
    return Checked(
        result=CheckResult(outcome=failing, total=0, passed=0, failed=0, failures=(failure,)),
        fills=counted(
            apply_fills({}, card["fills"], {scope: empty, "per_document": empty}, scope, [failure])
        ),
    )
