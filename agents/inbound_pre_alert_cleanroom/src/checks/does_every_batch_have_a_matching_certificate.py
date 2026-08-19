"""Every batch listed across the invoices has a certificate of analysis.

One ``each_has_matching`` criterion: the batch numbers on the invoices' line
items must each appear on some certificate. The counts are of *batches listed on
the invoice*, which the spec states twice — in the card's own instructions and
again as a negative, *"the count is of batches listed on the invoice, not of
certificates received"* — so a batch whose certificate arrived three times is
counted once and a certificate for a batch nobody listed is counted not at all.

Rows with no batch number are not examined. The card asks about *"every batch
number listed"*, and a line item that lists none has not listed a batch whose
certificate could be missing. That is the one reading of this card that changes
a number, and it is written down as an assumption rather than buried here.

``on_missing_input: fail`` is applied only to the side the criterion reads
*from*. No certificates at all is not a missing input — it is the finding this
Check exists to make, and short-circuiting it would report ``coa_total: 0`` for
a shipment whose invoices listed nine batches.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from checking import (
    Place,
    counted,
    evidence_for,
    missing_inputs,
    outcome_names,
    rows_for,
    scope_of,
)
from findings import Checked, Discrepancy
from matching import Matcher, has_match, same_batch

from meridian.runtime import CheckResult, EntityStore, Failure
from meridian.runtime.check import apply_fills, rows_failed, satisfied, tally
from meridian.runtime.check.counting import Tally
from meridian.runtime.check.paths import Row

_OP = "each_has_matching"

# How many certificate values a single failure carries as "what was available".
# Enough to show the near miss that explains the failure, few enough that a
# trace of a seventeen-certificate shipment is still readable.
_AVAILABLE_LIMIT = 12


def does_every_batch_have_a_matching_certificate(
    store: EntityStore,
    card: Mapping[str, Any],
    matcher: Matcher = same_batch,
) -> Checked:
    """Count the invoice batches with a certificate, and those without one.

    Args:
        store: every entity instance gathered for this shipment.
        card: this primitive's ``config`` from the frozen spec.
        matcher: how a listed batch and a certified batch are judged the same.
            Injected because the tolerance the spec settled is one sentence and
            the corpus may turn out to need another; a repair then swaps a
            function rather than unpicking a loop.

    Returns:
        The counts, the columns they fill, and the batches to report.
    """
    scope = scope_of(card)
    passing, failing = outcome_names(card)
    criterion = _sole_criterion(card)
    listed = str(criterion["left"]["entity"])

    absent = missing_inputs(store, [listed])
    if absent:
        return _nothing_arrived(card, failing, absent)

    rows = tuple(row for row in rows_for(store, criterion["left"]) if _is_listed(row))
    certified = _certified(store, criterion["right"])

    failures = [
        Failure(
            grain=scope,
            locator=row.locator,
            reason="no certificate of analysis carries this batch number",
            subject=str(row.value),
            detail={"value": row.value, "available": certified[:_AVAILABLE_LIMIT]},
        )
        for row in rows
        if not has_match(str(row.value), certified, matcher)
    ]

    failed: set[Place] = rows_failed(rows, failures)
    counts = tally(rows, failed)
    held = satisfied(str(card["quantifier"]), counts.passed, counts.total)
    result = CheckResult(
        outcome=passing if held else failing,
        total=counts.total,
        passed=counts.passed,
        failed=counts.failed,
        failures=tuple(failures),
    )
    return Checked(
        result=result,
        fills=counted(apply_fills({}, card["fills"], {scope: counts}, scope, failures)),
        discrepancies=tuple(
            Discrepancy(place, result.outcome, evidence_for(store, card["evidence"], place))
            for place in sorted(failed)
        ),
    )


def _sole_criterion(card: Mapping[str, Any]) -> Mapping[str, Any]:
    criteria = card["criteria"]
    if len(criteria) != 1 or criteria[0]["op"] != _OP:
        raise NotImplementedError(
            f"this Check implements one {_OP!r} criterion; the spec now declares "
            f"{len(criteria)} ({', '.join(c['op'] for c in criteria)})"
        )
    return dict(criteria[0])


def _is_listed(row: Row) -> bool:
    """Whether this line item actually named a batch.

    A blank is not a batch whose certificate is missing; it is a line item that
    named none. The four-codes Check is what reports incomplete line items.
    """
    return row.value is not None and bool(str(row.value).strip())


def _certified(store: EntityStore, right: Mapping[str, Any]) -> list[str]:
    """Every batch number the certificates on hand carry, deduplicated and exact.

    Duplicates collapse because the spec says so: *"If more than one certificate
    matches the same batch, the process may use any one of them."*

    **Values are not trimmed.** The criterion is ``each_has_matching``, and the
    one tolerance the process owner settled is the lot suffix; whitespace is not
    named anywhere. Normalising here would invent a rule and, worse, hide it: a
    batch that fails on a trailing space shows up in the trace as ``'UCB26016 '``
    against ``['UCB26016']``, which is a diagnosis a reader solves in one look.
    A blank is dropped because it is an absent value rather than a batch.
    """
    if right.get("kind") != "field":
        raise NotImplementedError(
            f"the right-hand side of this criterion is {right.get('kind')!r}; "
            "this Check compares against another entity's field"
        )
    values = (row.value for row in rows_for(store, right["field"]))
    return sorted({str(v) for v in values if v is not None and str(v).strip()})


def _nothing_arrived(card: Mapping[str, Any], failing: str, absent: Sequence[str]) -> Checked:
    """The failing outcome with honest zeros when the invoices never arrived."""
    scope = scope_of(card)
    failure = Failure(
        grain=scope,
        locator=absent[0],
        reason="a declared input never arrived, so no batch could be listed",
        subject=absent[0],
        detail={"missing_inputs": list(absent)},
    )
    empty = Tally(total=0, passed=0, failed=0)
    return Checked(
        result=CheckResult(outcome=failing, total=0, passed=0, failed=0, failures=(failure,)),
        fills=counted(apply_fills({}, card["fills"], {scope: empty}, scope, [failure])),
    )
