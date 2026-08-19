"""What both Checks do identically, so neither file restates it.

The check engine in ``meridian.runtime.check`` already resolves paths, counts
rows and rolls counts up. What it cannot know is how *this* spec addresses a
field — as ``{entity, path}`` pairs — or how many documents a shipment actually
received. These functions are that adaptation and nothing more.

``Scope`` is imported from ``meridian.domain.primitives`` because
``runtime.Failure.grain`` is typed as it and the runtime re-exports no alias.
One import, in one file, rather than a cast at every call site that would make
the type checker agree without making the code true.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

from meridian.domain.primitives import Scope
from meridian.runtime import EntityStore
from meridian.runtime.check import Row, resolve
from meridian.runtime.check.counting import Tally

Place = tuple[int, tuple[int, ...]]
"""One thing examined: which document, and where inside it.

Keyed this way rather than by locator because two criteria failing on one line
item are one failed line item, and a locator would count that twice.
"""


def scope_of(card: Mapping[str, Any]) -> Scope:
    """The grain one row of this Check represents, as the runtime types it."""
    return cast("Scope", str(card["scope"]))


def rows_for(store: EntityStore, field: Mapping[str, Any]) -> tuple[Row, ...]:
    """Every row a spec field reference addresses, across a shipment's documents."""
    name = str(field["entity"])
    return resolve(name, list(store.instances(name)), str(field["path"]))


def outcome_names(card: Mapping[str, Any]) -> tuple[str, str]:
    """The passing outcome and the failing one, lowest declared priority first.

    Read off the card rather than written down, so a renamed outcome renames the
    routing literal and the trace together — they are the same string.
    """
    ordered = sorted(card["outcomes"], key=lambda o: int(o["priority"]))
    return str(ordered[0]["name"]), str(ordered[-1]["name"])


def roll_up_over_instances(failed: set[Place], instances: int) -> Tally:
    """Per-document counts, taken over the documents that *arrived*.

    ``runtime.check.roll_up`` counts the documents that produced rows, which is
    the right default and the wrong answer here: an invoice extracted with an
    empty line-item list would silently vanish from ``invoices_total``, and a
    column short by one because extraction failed looks exactly like a column
    short by one because the shipment had fewer invoices. Counting the instances
    keeps the extraction failure visible where it happened.
    """
    bad = {document for document, _ in failed}
    return Tally(total=instances, passed=instances - len(bad), failed=len(bad))


def evidence_for(store: EntityStore, evidence: Sequence[Mapping[str, Any]], place: Place) -> str:
    """The card's own ``evidence`` fields, read at one failing place.

    The Action names the invoice and the batch; ``evidence`` is what the *Check*
    said proves the problem, and it is what makes a failure readable without
    opening a PDF.
    """
    document, indices = place
    parts: list[str] = []
    for field in evidence:
        label = str(field["path"]).rsplit(".", 1)[-1]
        for row in rows_for(store, field):
            # A scalar on the document (an invoice number) has no indices and
            # applies to every place inside it; an iterated field applies only
            # to the place it came from.
            if row.document == document and row.indices in ((), indices) and row.value is not None:
                parts.append(f"{label}={row.value}")
    return ", ".join(dict.fromkeys(parts))


def missing_inputs(store: EntityStore, inputs: Sequence[str]) -> tuple[str, ...]:
    """Which of a Check's declared inputs never arrived."""
    return tuple(name for name in inputs if not store.instances(name))


def counted(fills: Mapping[str, Any]) -> dict[str, int]:
    """A filled row narrowed to the integers the output entity declares.

    Every measure on this board is a count. A ``failing`` measure would put a
    list here, and the loud failure is the point: a column silently changing
    type is how an eval row starts comparing a list against a number.
    """
    wrong = {k: v for k, v in fills.items() if not isinstance(v, int)}
    if wrong:
        raise TypeError(f"a column that is not a count cannot go in the output row: {wrong}")
    return {str(k): int(v) for k, v in fills.items()}
