"""What a Check hands the rest of the process besides its counts.

``CheckResult`` is fixed by the runtime and the eval row is arithmetic over it,
so nothing may be added to it. But two consumers need more than counts: the
output row needs the Check's ``fills`` applied at the right grain, and the
report Action needs to know *which* line items are wrong so it can name their
invoice and batch numbers rather than tallying them.

Both are derived from the same rows the counts came from, which is why they are
produced together — recomputing them in the Action would resolve the same paths
a second time and could disagree with the number that was reported.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from checking import Place

from meridian.runtime import CheckResult


@dataclass(frozen=True)
class Discrepancy:
    """One line item that failed, located well enough to be named in an email.

    ``kind`` is the Check's own failing outcome name — ``missing_information``
    or ``missing_coa`` — because that is the only vocabulary for "what sort of
    problem this is" that the process owner actually declared. The spec asks the
    Action to deduplicate on *"container_no + batch_no + discrepancy_kind"*, and
    this is the third of those three.
    """

    place: Place
    kind: str
    evidence: str


@dataclass(frozen=True)
class Checked:
    """Everything one Check produced, in one value.

    Returned as a bundle rather than through three calls so that the counts, the
    columns and the things to report can never be computed from different rows.
    """

    result: CheckResult
    fills: dict[str, int] = field(default_factory=dict)
    discrepancies: tuple[Discrepancy, ...] = ()
